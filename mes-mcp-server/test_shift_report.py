"""test_shift_report.py — 班次巡检简报的验收集合

覆盖：
  - 班次口径：08:00 边界取「刚结束的晚班」20:00→08:00；20:00 取早班 08:00→20:00；
    非边界时刻取「当班至今」，不会把还没过完的班当成已收班
  - 统计口径：越阈按窗口内**峰值**判（中途越阈后回落也要曝光）；
    done（待闭环）计入未闭环；窗口外的采集与工单不串班
  - 历史还原：补发旧班次时，工单状态按 work_order_log 回推到收班时刻，
    不会因为今天有人关单而把当时的挂单抹掉
  - 渲染：无异常时有明确的「无 ✅」文案；有数据时关键信息都在
  - 推送：未配置飞书通道时返回失败但不抛异常；配置了就发卡片
  - 定时：next_boundary 在 07:59 / 08:00 / 21:00 三种时刻的落点

运行：python3 -m unittest test_shift_report -v（使用临时库，不污染演示数据）
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import feishu  # noqa: E402
import init_db  # noqa: E402
import shift_report  # noqa: E402


class TempDbCase(unittest.TestCase):
    """每个用例一套临时库，避免覆盖 data/mes.db 里的演示数据"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="mes-shift-test-"))
        self._orig = (db.DATA_DIR, db.DB_PATH)
        db.DATA_DIR = self._tmp
        db.DB_PATH = self._tmp / "mes.db"
        conn = db.get_db()
        conn.executescript(init_db.SCHEMA)
        init_db.seed(conn)
        # 种子里的指标/工单/知识是按「当前时刻」造的，会让窗口断言不稳定；
        # 简报测试要的是可控数据，只保留设备台账，其余清空后按用例自己铺。
        for table in ("work_order_log", "work_order", "knowledge", "metric"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        db.DATA_DIR, db.DB_PATH = self._orig

    def _metric(self, eq: str, ts: str, **values) -> None:
        db.insert_metric(eq, ts=ts, **values)

    def _wo(self, eq: str, created_at: str, status: str = "open",
            severity: str = "high") -> str:
        """建一张指定创建时刻的工单，并按 status 补写对应的流转日志"""
        wo_id = db._new_id("WO")
        with db.get_db() as conn:
            conn.execute(
                "INSERT INTO work_order(wo_id, equipment_id, fault_desc, severity, status, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (wo_id, eq, "测试工单", severity, "open", created_at, created_at),
            )
            path = {
                "open": [],
                "assigned": ["assigned"],
                "done": ["assigned", "done"],
                "closed": ["assigned", "done", "closed"],
            }[status]
            previous = "open"
            for step in path:
                conn.execute(
                    "INSERT INTO work_order_log(wo_id, action, detail, operator, ts) "
                    "VALUES (?,?,?,?,?)",
                    (wo_id, "status_change", f"{previous} → {step}", "测试", created_at),
                )
                previous = step
            conn.execute(
                "UPDATE work_order SET status = ? WHERE wo_id = ?", (status, wo_id)
            )
        return wo_id


class WindowTests(unittest.TestCase):
    """班次边界：早班 08:00–20:00，晚班 20:00–次日 08:00"""

    def test_report_at_0800_is_the_night_shift_that_just_ended(self) -> None:
        start, end, name, ended = shift_report.shift_window(
            datetime(2026, 11, 7, 8, 0, 0), ended=True
        )
        self.assertEqual(name, "晚班")
        self.assertEqual(start, datetime(2026, 11, 6, 20, 0, 0))
        self.assertEqual(end, datetime(2026, 11, 7, 8, 0, 0))
        self.assertTrue(ended)

    def test_report_at_2000_is_the_day_shift_that_just_ended(self) -> None:
        start, end, name, ended = shift_report.shift_window(
            datetime(2026, 11, 7, 20, 0, 0), ended=True
        )
        self.assertEqual(name, "早班")
        self.assertEqual(start, datetime(2026, 11, 7, 8, 0, 0))
        self.assertEqual(end, datetime(2026, 11, 7, 20, 0, 0))
        self.assertTrue(ended)

    def test_mid_shift_window_runs_from_shift_start_to_now(self) -> None:
        start, end, name, ended = shift_report.shift_window(
            datetime(2026, 11, 7, 10, 30, 0)
        )
        self.assertEqual(name, "早班")
        self.assertEqual(start, datetime(2026, 11, 7, 8, 0, 0))
        self.assertEqual(end, datetime(2026, 11, 7, 10, 30, 0))
        # 还没收班，不能被当成完整班次
        self.assertFalse(ended)

    def test_after_midnight_still_belongs_to_night_shift(self) -> None:
        start, end, name, _ = shift_report.shift_window(datetime(2026, 11, 7, 3, 0, 0))
        self.assertEqual(name, "晚班")
        self.assertEqual(start, datetime(2026, 11, 6, 20, 0, 0))
        self.assertEqual(end, datetime(2026, 11, 7, 3, 0, 0))

    def test_next_boundary_rolls_to_next_day_after_evening(self) -> None:
        self.assertEqual(
            shift_report.next_boundary(datetime(2026, 11, 7, 7, 59)), 
            datetime(2026, 11, 7, 8, 0, 0),
        )
        self.assertEqual(
            shift_report.next_boundary(datetime(2026, 11, 7, 8, 0)),
            datetime(2026, 11, 7, 20, 0, 0),
        )
        self.assertEqual(
            shift_report.next_boundary(datetime(2026, 11, 7, 21, 0)),
            datetime(2026, 11, 8, 8, 0, 0),
        )


class CollectTests(TempDbCase):
    def test_peak_in_window_counts_even_if_recovered(self) -> None:
        """中途越阈、收班前已回落的设备同样要曝光 —— 这是简报存在的理由"""
        self._metric("INJ-01", "2026-11-07 09:00:00", temp=80.0, vibration=3.0)
        self._metric("INJ-01", "2026-11-07 19:50:00", temp=60.0, vibration=3.0)
        report = shift_report.collect(datetime(2026, 11, 7, 20, 0, 0), ended=True)
        ids = [d["equipment_id"] for d in report["devices_over"]]
        self.assertIn("INJ-01", ids)
        device = next(d for d in report["devices_over"] if d["equipment_id"] == "INJ-01")
        self.assertEqual(device["items"][0]["peak"], 80.0)
        # 收班那一刻已经不越阈了 → 不能标「仍越阈」
        self.assertEqual(device["over_now"], [])
        self.assertEqual(report["summary"]["devices_over_now"], 0)

    def test_metrics_outside_window_do_not_leak_in(self) -> None:
        self._metric("INJ-01", "2026-11-07 07:00:00", temp=90.0)  # 属晚班
        self._metric("INJ-01", "2026-11-07 09:00:00", temp=60.0)
        report = shift_report.collect(datetime(2026, 11, 7, 20, 0, 0), ended=True)
        self.assertEqual(report["devices_over"], [])
        self.assertEqual(report["summary"]["samples"], 1)

    def test_done_counts_as_unclosed(self) -> None:
        """done 是「已处理待闭环」，仍然要人签字，不能从待办里消失"""
        self._wo("CNC-01", "2026-11-07 09:00:00", status="done")
        report = shift_report.collect(datetime(2026, 11, 7, 20, 0, 0), ended=True)
        self.assertEqual(report["summary"]["wo_open"], 1)
        self.assertEqual(report["open_work_orders"][0]["status_label"], "已处理，待闭环")

    def test_closed_within_window_counted_but_not_listed(self) -> None:
        wo_id = self._wo("CNC-01", "2026-11-07 09:00:00", status="closed")
        with db.get_db() as conn:  # 关单日志落在班次内，创建在班次前
            conn.execute("UPDATE work_order SET created_at = ? WHERE wo_id = ?",
                         ("2026-11-07 09:00:00", wo_id))
            conn.execute(
                "INSERT INTO work_order_log(wo_id, action, detail, operator, ts) "
                "VALUES (?,?,?,?,?)",
                (wo_id, "status_change", "done → closed", "测试", "2026-11-07 15:00:00"),
            )
        report = shift_report.collect(datetime(2026, 11, 7, 20, 0, 0), ended=True)
        self.assertEqual(report["summary"]["wo_closed"], 1)
        self.assertEqual(report["summary"]["wo_open"], 0)
        self.assertEqual(report["closed_work_orders"][0]["wo_id"], wo_id)

    def test_history_is_restored_from_log_not_current_status(self) -> None:
        """补发旧班次：今天关掉的单，不能从当时那份简报里消失"""
        wo_id = self._wo("CNC-01", "2026-11-06 21:00:00", status="open")
        # 昨天晚班的简报里它是挂着的
        night = shift_report.collect(datetime(2026, 11, 7, 8, 0, 0), ended=True)
        self.assertEqual([w["wo_id"] for w in night["open_work_orders"]], [wo_id])
        # 今天才闭环
        db.close_work_order(wo_id, "更换轴承", operator="王师傅")
        again = shift_report.collect(datetime(2026, 11, 7, 8, 0, 0), ended=True)
        self.assertEqual([w["wo_id"] for w in again["open_work_orders"]], [wo_id])
        self.assertEqual(again["summary"]["wo_open"], 1)

    def test_work_order_created_after_shift_is_excluded(self) -> None:
        self._wo("CNC-01", "2026-11-07 21:00:00", status="open")
        report = shift_report.collect(datetime(2026, 11, 7, 20, 0, 0), ended=True)
        self.assertEqual(report["summary"]["wo_open"], 0)
        self.assertEqual(report["summary"]["wo_created"], 0)

    def test_knowledge_sedimented_in_window_is_summarized(self) -> None:
        db.save_knowledge("HEATER-AGING", "INJ-01 温度偏高", "加热圈老化", "更换加热圈")
        with db.get_db() as conn:
            conn.execute(
                "UPDATE knowledge SET created_at = ?", ("2026-11-07 19:00:00",)
            )
        report = shift_report.collect(datetime(2026, 11, 7, 20, 0, 0), ended=True)
        self.assertEqual(report["summary"]["knowledge_added"], 1)
        self.assertIn("HEATER-AGING", shift_report.render_markdown(report))

    def test_empty_shift_says_clean_explicitly(self) -> None:
        report = shift_report.collect(datetime(2026, 11, 7, 20, 0, 0), ended=True)
        md = shift_report.render_markdown(report)
        self.assertIn("无，全设备指标在阈值内 ✅", md)
        self.assertIn("无，当班工单均已闭环 ✅", md)
        self.assertIn("当班无采集数据", md)

    def test_markdown_carries_key_facts(self) -> None:
        self._metric("CNC-03", "2026-11-07 18:10:00", vibration=9.5, temp=50.0)
        self._metric("CNC-03", "2026-11-07 19:59:00", vibration=9.1, temp=50.0)
        wo_id = self._wo("CNC-03", "2026-11-07 09:00:00", status="assigned")
        report = shift_report.collect(datetime(2026, 11, 7, 20, 0, 0), ended=True)
        md = shift_report.render_markdown(report)
        self.assertIn("早班 08:00–20:00", md)
        self.assertIn("CNC-03", md)
        self.assertIn("振动峰值 9.5 mm/s", md)
        self.assertIn("收班时仍越阈", md)
        self.assertIn(wo_id, md)
        self.assertIn("收班时仍越阈 1 台", md)


class SendTests(TempDbCase):
    def test_without_feishu_config_send_fails_softly(self) -> None:
        with mock.patch.object(feishu, "APP_ID", ""), \
             mock.patch.object(feishu, "APP_SECRET", ""), \
             mock.patch.object(feishu, "CHAT_ID", ""), \
             mock.patch.object(feishu, "WEBHOOK_URL", ""):
            ok, note, report = shift_report.send_shift_report(
                datetime(2026, 11, 7, 20, 0, 0), ended=True
            )
        self.assertFalse(ok)
        self.assertIn("未配置飞书通道", note)
        self.assertEqual(report["shift"], "早班")

    def test_card_is_sent_through_app_channel(self) -> None:
        sent: dict = {}

        def fake_send(card):
            sent["card"] = card
            return True, "已推送（自建应用）"

        with mock.patch.object(feishu, "app_bot_enabled", return_value=True), \
             mock.patch.object(feishu, "send_card_via_app", side_effect=fake_send):
            ok, note, _ = shift_report.send_shift_report(
                datetime(2026, 11, 7, 20, 0, 0), ended=True
            )
        self.assertTrue(ok)
        self.assertEqual(sent["card"]["schema"], "2.0")
        self.assertIn("早班巡检简报", sent["card"]["header"]["title"]["content"])

    def test_dry_run_never_touches_network(self) -> None:
        with mock.patch.object(feishu, "send_card_via_app") as sender:
            ok, note, _ = shift_report.send_shift_report(
                datetime(2026, 11, 7, 20, 0, 0), ended=True, dry_run=True
            )
        self.assertTrue(ok)
        self.assertIn("dry-run", note)
        sender.assert_not_called()

    def test_push_exception_does_not_raise(self) -> None:
        """推送炸了只记日志，定时器还得继续活着等下一班"""
        with mock.patch.object(feishu, "app_bot_enabled", return_value=True), \
             mock.patch.object(feishu, "send_card_via_app", side_effect=RuntimeError("boom")):
            ok, note, report = shift_report.send_shift_report(
                datetime(2026, 11, 7, 20, 0, 0), ended=True
            )
        self.assertFalse(ok)
        self.assertIn("boom", note)
        self.assertEqual(report["shift"], "早班")


if __name__ == "__main__":
    unittest.main()
