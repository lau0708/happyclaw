"""test_ingest.py — 采集入库（A）+ 工况模拟器（C）的验收集合

覆盖：
  - 入库校验：设备编码必须已登记、数值必须落在物理区间、时间戳归一
  - 幂等：同设备同秒重复上报只留一条（网关重试不放大点数）
  - 鉴权：配置 INGEST_API_KEY 后必须带 X-API-Key；未配置时只放行本机
  - 批量补传：坏点不阻断好点，重复计入 duplicates
  - 模拟器：覆盖全部设备与字段区间、同种子可复现、故障设备振动单调劣化、
    劣化到阈值后能被巡检判定并建单、回填模式不注入故障

运行：python3 -m unittest test_ingest -v（使用临时库，不污染演示数据）
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api  # noqa: E402
import agent_nodes  # noqa: E402
import db  # noqa: E402
import feishu  # noqa: E402
import init_db  # noqa: E402
import scheduler  # noqa: E402
import simulator  # noqa: E402


class TempDbCase(unittest.TestCase):
    """每个用例一套临时库，避免覆盖 data/mes.db 里的演示数据"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="mes-ingest-test-"))
        self._orig = (db.DATA_DIR, db.DB_PATH)
        db.DATA_DIR = self._tmp
        db.DB_PATH = self._tmp / "mes.db"
        conn = db.get_db()
        conn.executescript(init_db.SCHEMA)
        init_db.seed(conn)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        db.DATA_DIR, db.DB_PATH = self._orig


class MetricValidationTests(TempDbCase):
    def test_insert_creates_row(self) -> None:
        row = db.insert_metric(
            "CNC-01", temp=60.5, vibration=6.1, pressure=4.0, rpm=3100, run_hours=14201
        )
        self.assertFalse(row["duplicate"])
        self.assertTrue(row["id"].startswith("M-CNC-01-"))
        stored = db.latest_metric_row("CNC-01")
        self.assertAlmostEqual(stored["temp"], 60.5)
        self.assertAlmostEqual(stored["vibration"], 6.1)
        self.assertEqual(stored["rpm"], 3100)

    def test_same_second_is_idempotent(self) -> None:
        ts = "2026-11-06 20:00:00"
        first = db.insert_metric("CNC-01", ts=ts, vibration=6.2)
        second = db.insert_metric("CNC-01", ts=ts, vibration=6.9)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        rows = [r for r in db.query_metric("CNC-01", hours=10000) if r["ts"] == ts]
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["vibration"], 6.2)  # 先到的为准，不被重试覆盖

    def test_unknown_equipment_rejected(self) -> None:
        with self.assertRaises(db.EquipmentNotFound):
            db.insert_metric("CNC-99", vibration=6.0)

    def test_missing_equipment_id_rejected(self) -> None:
        with self.assertRaises(db.MetricError):
            db.insert_metric(None, vibration=6.0)

    def test_out_of_range_rejected(self) -> None:
        with self.assertRaises(db.MetricError) as ctx:
            db.insert_metric("CNC-01", vibration=999.0)
        self.assertIn("超出物理合理区间", str(ctx.exception))
        with self.assertRaises(db.MetricError):
            db.insert_metric("CNC-01", temp=-100.0)

    def test_non_numeric_rejected(self) -> None:
        with self.assertRaises(db.MetricError):
            db.insert_metric("CNC-01", temp="abc")
        with self.assertRaises(db.MetricError):  # 布尔值不算数字（True 会被当成 1）
            db.insert_metric("CNC-01", vibration=True)

    def test_requires_at_least_one_metric(self) -> None:
        with self.assertRaises(db.MetricError):
            db.insert_metric("CNC-01")

    def test_iso_timestamp_normalized(self) -> None:
        row = db.insert_metric("CNC-01", ts="2026-11-06T18:30:00.123Z", vibration=6.0)
        self.assertEqual(row["ts"], "2026-11-06 18:30:00")

    def test_bad_timestamp_rejected(self) -> None:
        with self.assertRaises(db.MetricError):
            db.insert_metric("CNC-01", ts="昨天下午", vibration=6.0)

    def test_ingested_point_drives_inspection(self) -> None:
        """入库的点位必须能被巡检读到（否则采集链路等于没接通）"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.insert_metric("CNC-01", ts=now, vibration=9.4, temp=60.0, pressure=4.0)
        check = db.check_latest_metric("CNC-01")
        self.assertIn("vibration", check["over_threshold"])
        self.assertAlmostEqual(check["metric"]["vibration"], 9.4)


class IngestApiTests(TempDbCase):
    def _payload(self, **overrides) -> dict:
        payload = {"equipment_id": "CNC-01", "temp": 60.0, "vibration": 6.1}
        payload.update(overrides)
        return payload

    def test_requires_key_when_configured(self) -> None:
        with mock.patch.dict(os.environ, {"INGEST_API_KEY": "s3cret"}):
            code, data = api.handle("/api/metric", {}, self._payload(), headers={}, client_ip="10.0.0.9")
            self.assertEqual(code, 401)
            self.assertIn("X-API-Key", data["error"])

    def test_matching_key_accepted(self) -> None:
        with mock.patch.dict(os.environ, {"INGEST_API_KEY": "s3cret"}):
            code, data = api.handle(
                "/api/metric", {}, self._payload(),
                headers={"X-API-Key": "s3cret"}, client_ip="10.0.0.9",
            )
            self.assertEqual(code, 200)
            self.assertFalse(data["duplicate"])

    def test_wrong_key_rejected(self) -> None:
        with mock.patch.dict(os.environ, {"INGEST_API_KEY": "s3cret"}):
            code, _ = api.handle(
                "/api/metric", {}, self._payload(),
                headers={"x-api-key": "wrong"}, client_ip="127.0.0.1",
            )
            self.assertEqual(code, 401)  # 配了密钥就一律校验，本机也不例外

    def test_loopback_allowed_without_key(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("INGEST_API_KEY", None)
            code, data = api.handle("/api/metric", {}, self._payload(), headers={}, client_ip="127.0.0.1")
            self.assertEqual(code, 200)
            self.assertFalse(data["duplicate"])

    def test_remote_rejected_without_key(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("INGEST_API_KEY", None)
            code, data = api.handle("/api/metric", {}, self._payload(), headers={}, client_ip="10.0.0.9")
            self.assertEqual(code, 403)
            self.assertIn("INGEST_API_KEY", data["error"])

    def test_unknown_device_returns_404(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("INGEST_API_KEY", None)
            code, _ = api.handle("/api/metric", {}, self._payload(equipment_id="CNC-99"))
            self.assertEqual(code, 404)

    def test_dirty_value_returns_400(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("INGEST_API_KEY", None)
            code, data = api.handle("/api/metric", {}, self._payload(vibration=8888))
            self.assertEqual(code, 400)
            self.assertIn("区间", data["error"])

    def test_get_metric_returns_usage_hint(self) -> None:
        code, data = api.handle("/api/metric", {})
        self.assertEqual(code, 405)
        self.assertIn("POST", data["error"])

    def test_batch_accepts_and_dedupes(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("INGEST_API_KEY", None)
            body = {
                "metrics": [
                    {"equipment_id": "CNC-01", "ts": "2026-11-06 21:00:00", "vibration": 6.0},
                    {"equipment_id": "CNC-01", "ts": "2026-11-06 21:00:00", "vibration": 6.0},
                    {"equipment_id": "CNC-01", "ts": "2026-11-06 21:01:00", "vibration": 6.1},
                    {"equipment_id": "CNC-99", "ts": "2026-11-06 21:01:00", "vibration": 6.1},
                ]
            }
            code, data = api.handle("/api/metric/batch", {}, body)
            self.assertEqual(code, 200)
            self.assertEqual(data["received"], 4)
            self.assertEqual(data["accepted"], 2)
            self.assertEqual(data["duplicates"], 1)
            self.assertEqual(data["rejected"], 1)

    def test_batch_requires_list(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("INGEST_API_KEY", None)
            code, data = api.handle("/api/metric/batch", {}, {"metrics": "oops"})
            self.assertEqual(code, 400)
            self.assertIn("metrics", data["error"])

    def test_health_reports_auth_mode(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("INGEST_API_KEY", None)
            code, data = api.handle("/api/health", {})
            self.assertEqual(code, 200)
            self.assertEqual(data["ingest_auth"], "loopback-only")


class SimulatorTests(TempDbCase):
    def _sim(self, **kwargs) -> simulator.Simulator:
        kwargs.setdefault("seed", 7)
        return simulator.Simulator(db.query_equipment(), **kwargs)

    def test_covers_all_devices_within_physical_ranges(self) -> None:
        rows = self._sim().sample_all(datetime.now(), 10 / 3600)
        ids = {row["equipment_id"] for row in rows}
        self.assertEqual(ids, {"CNC-01", "CNC-03", "INJ-01", "ASM-01"})
        for row in rows:
            for field in ("temp", "vibration", "pressure", "rpm", "run_hours"):
                low, high = db.METRIC_RANGES[field]
                self.assertGreaterEqual(row[field], low, f"{row['equipment_id']}.{field}")
                self.assertLessEqual(row[field], high, f"{row['equipment_id']}.{field}")

    def test_same_seed_is_reproducible(self) -> None:
        ts = datetime(2026, 11, 6, 20, 0, 0)
        a = self._sim(seed=3).sample_all(ts, 0.5)
        b = self._sim(seed=3).sample_all(ts, 0.5)
        self.assertEqual(a, b)

    def test_run_hours_increases(self) -> None:
        sim = self._sim()
        ts = datetime.now()
        first = {r["equipment_id"]: r["run_hours"] for r in sim.sample_all(ts, 1.0)}
        later = {r["equipment_id"]: r["run_hours"] for r in sim.sample_all(ts + timedelta(hours=1), 1.0)}
        for eq, hours in first.items():
            self.assertGreaterEqual(later[eq], hours + 1)

    def test_fault_device_vibration_drifts_up(self) -> None:
        sim = self._sim(fault="CNC-03:vibration", fault_rate=6.0)
        ts = datetime(2026, 11, 6, 12, 0, 0)
        series = [
            next(r for r in sim.sample_all(ts + timedelta(minutes=i), 1 / 60) if r["equipment_id"] == "CNC-03")
            for i in range(30)
        ]
        self.assertGreater(series[-1]["vibration"], series[0]["vibration"] + 1.5)

    def test_healthy_device_stays_flat(self) -> None:
        sim = self._sim(fault="CNC-03:vibration", fault_rate=6.0)
        ts = datetime(2026, 11, 6, 12, 0, 0)
        series = [
            next(r for r in sim.sample_all(ts + timedelta(minutes=i), 1 / 60) if r["equipment_id"] == "CNC-01")
            for i in range(30)
        ]
        self.assertLess(abs(series[-1]["vibration"] - series[0]["vibration"]), 1.0)

    def test_backfill_does_not_inject_fault(self) -> None:
        sim = self._sim(fault="CNC-03:vibration", fault_rate=6.0)
        model = next(m for m in sim.models if m.equipment_id == "CNC-03")
        ts = datetime(2026, 11, 6, 0, 0, 0)
        for i in range(40):
            sim.sample_all(ts + timedelta(minutes=5 * i), 5 / 60, apply_fault=False)
        self.assertEqual(model.drift["vibration"], 0.0)

    def test_degradation_reaches_threshold_and_creates_order(self) -> None:
        """端到端：模拟劣化 → 入库 → 巡检判异 → 建单（不调用真实 LLM / 飞书）"""
        sim = self._sim(fault="CNC-03:vibration", fault_rate=30.0)
        sender = simulator.DirectSender()
        ts = datetime.now()
        for i in range(40):  # 30/小时 × 40 分钟 ≈ +20 mm/s
            sender(sim.sample_all(ts + timedelta(minutes=i), 1 / 60))

        check = db.check_latest_metric("CNC-03")
        self.assertIn("vibration", check["over_threshold"])
        before = len(db.query_work_orders(equipment_id="CNC-03"))
        with mock.patch.object(agent_nodes, "LLM_API_KEY", ""), \
                mock.patch.object(feishu, "WEBHOOK_URL", ""):
            scheduler.inspect_device("CNC-03")
        self.assertEqual(len(db.query_work_orders(equipment_id="CNC-03")), before + 1)

    def test_direct_sender_counts_duplicates(self) -> None:
        sim = self._sim(fault=None)
        rows = sim.sample_all(datetime(2026, 11, 6, 9, 0, 0), 0.0)
        sender = simulator.DirectSender()
        first = sender(rows)
        second = sender(rows)
        self.assertEqual(first["accepted"], len(rows))
        self.assertEqual(second["duplicates"], len(rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
