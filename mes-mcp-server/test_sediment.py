"""test_sediment.py — 工单闭环 → 知识库自动沉淀 的验收集合

覆盖：
  - 关单即沉淀：故障码推断、症状带设备标识、处置取自回填、source_wo_id 溯源
  - 幂等：同一张工单关两次 / 重复关单 API 调用，只产出一条知识
  - 边界：未闭环的工单不沉淀（没有处置结果，沉淀＝废料）
  - 开关：MES_AUTO_KNOWLEDGE=0 时完全不写
  - 容错：沉淀过程抛异常也只记日志，关单本身必须成功
  - 契约：API 返回的工单带 knowledge_id，知识库按关键词能搜到新条目

运行：python3 -m unittest test_sediment -v（使用临时库，不污染演示数据）
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api  # noqa: E402
import db  # noqa: E402
import init_db  # noqa: E402


class TempDbCase(unittest.TestCase):
    """每个用例一套临时库，避免覆盖 data/mes.db 里的演示数据"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="mes-sediment-test-"))
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

    # -- 小工具 ---------------------------------------------------------- #
    def _new_order(self, equipment_id: str, desc: str) -> str:
        return db.create_work_order(equipment_id, desc, severity="high")["wo_id"]

    def _knowledge_count(self) -> int:
        with db.get_db() as conn:
            return conn.execute("SELECT COUNT(*) c FROM knowledge").fetchone()["c"]

    def _logs(self, wo_id: str) -> list[dict]:
        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT action, detail FROM work_order_log WHERE wo_id = ? ORDER BY ts, rowid",
                (wo_id,),
            ).fetchall()
        return [dict(r) for r in rows]


class FaultCodeTests(unittest.TestCase):
    """故障码推断：顺序即优先级，别的词不能抢走更具体的判断"""

    def test_specific_bearing_wins_over_symptom_word(self) -> None:
        self.assertEqual(
            db.infer_fault_code("主轴振动 12.7mm/s 超阈，疑似主轴轴承磨损复发"),
            "SPINDLE-BEARING",
        )

    def test_threshold_words(self) -> None:
        self.assertEqual(db.infer_fault_code("料筒温度 69℃ 偏高"), "TEMP-OVER")
        self.assertEqual(db.infer_fault_code("振动 8.6mm/s 超限"), "VIB-OVER")
        self.assertEqual(db.infer_fault_code("液压压力不足"), "HYDRAULIC-FAULT")

    def test_unknown_falls_back_to_other(self) -> None:
        self.assertEqual(db.infer_fault_code("操作面板黑屏"), "OTHER")
        self.assertEqual(db.infer_fault_code(None, None), "OTHER")


class SedimentTests(TempDbCase):
    def test_close_creates_one_knowledge_entry(self) -> None:
        wo_id = self._new_order(
            "CNC-03", "主轴振动 12.7mm/s 超阈(8.0) 达 59%，疑似主轴轴承磨损复发"
        )
        db.update_work_order(wo_id, status="done", resolution="更换主轴轴承并复测振动", operator="王师傅")
        before = self._knowledge_count()

        # 关单不回填处置（飞书卡片就是这样点的）：不能把 done 环节的方案盖掉
        db.close_work_order(wo_id, "", operator="王师傅")

        self.assertEqual(self._knowledge_count(), before + 1)
        kn = db.knowledge_for_work_order(wo_id)
        self.assertIsNotNone(kn)
        assert kn is not None
        self.assertEqual(kn["source_wo_id"], wo_id)
        self.assertEqual(kn["fault_code"], "SPINDLE-BEARING")
        # 症状要带设备编号：按设备检索同类故障时才命得中
        self.assertIn("CNC-03", kn["symptom"])
        self.assertIn("振动", kn["symptom"])
        # 根因从「疑似…」从句里切出来，而不是把整段描述抄两遍
        self.assertIn("主轴轴承磨损复发", kn["root_cause"])
        self.assertEqual(kn["resolution"], "更换主轴轴承并复测振动")

        # 工单时间线上留痕：点开工单能看到这次闭环产出了哪条经验
        actions = [log["action"] for log in self._logs(wo_id)]
        self.assertIn("knowledge_sediment", actions)
        detail = next(log["detail"] for log in self._logs(wo_id) if log["action"] == "knowledge_sediment")
        self.assertIn(kn["id"], detail)

    def test_close_is_idempotent_for_knowledge(self) -> None:
        wo_id = self._new_order("CNC-03", "主轴振动超限，疑似轴承磨损")
        db.close_work_order(wo_id, "已修复", operator="测试")
        first = self._knowledge_count()
        db.close_work_order(wo_id, "已修复", operator="测试")  # 再关一次
        self.assertEqual(self._knowledge_count(), first)

    def test_open_order_never_sediments(self) -> None:
        wo_id = self._new_order("CNC-03", "主轴振动超限")
        self.assertIsNone(db.sediment_knowledge_from_work_order(wo_id))
        self.assertIsNone(db.knowledge_for_work_order(wo_id))

    def test_switch_off_disables_sediment(self) -> None:
        wo_id = self._new_order("CNC-03", "主轴振动超限，疑似轴承磨损")
        before = self._knowledge_count()
        with mock.patch.dict(os.environ, {db.AUTO_KNOWLEDGE_ENV: "0"}):
            db.close_work_order(wo_id, "已修复", operator="测试")
        self.assertEqual(self._knowledge_count(), before)

    def test_sediment_failure_never_blocks_close(self) -> None:
        wo_id = self._new_order("CNC-03", "主轴振动超限，疑似轴承磨损")
        with mock.patch.object(db, "save_knowledge", side_effect=RuntimeError("disk full")):
            wo = db.close_work_order(wo_id, "已修复", operator="测试")
        assert wo is not None
        self.assertEqual(wo["status"], "closed")

    def test_resolution_falls_back_to_timeline(self) -> None:
        """卡片关单路径不回填 resolution 时，处置取自 done 环节的时间线记录"""
        wo_id = self._new_order("INJ-01", "料筒温度偏高，疑似加热圈老化")
        db.update_work_order(wo_id, status="done", resolution="更换加热圈并复校温控", operator="李工")
        db.close_work_order(wo_id, "", operator="李工")
        kn = db.knowledge_for_work_order(wo_id)
        assert kn is not None
        self.assertEqual(kn["resolution"], "更换加热圈并复校温控")

    def test_explicit_resolution_at_close_wins(self) -> None:
        """关单时确实回填了处置结果，则以它为准（人工补充的最终结论更可信）"""
        wo_id = self._new_order("CNC-03", "主轴振动超限，疑似轴承磨损")
        db.update_work_order(wo_id, status="done", resolution="初判：先降速观察", operator="王师傅")
        db.close_work_order(wo_id, "换轴承后复测 4.2mm/s，确认修复", operator="王师傅")
        kn = db.knowledge_for_work_order(wo_id)
        assert kn is not None
        self.assertEqual(kn["resolution"], "换轴承后复测 4.2mm/s，确认修复")


class ApiContractTests(TempDbCase):
    def test_work_order_response_carries_knowledge_id(self) -> None:
        wo_id = self._new_order("CNC-03", "主轴振动超限，疑似轴承磨损")
        code, closed = api.handle(f"/api/work_orders/{wo_id}/close", {}, {"operator": "卡片用户"})
        self.assertEqual(code, 200)
        assert isinstance(closed, dict)
        self.assertIn("knowledge_id", closed)
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["knowledge_fault_code"], "SPINDLE-BEARING")

        # 再查一次（卡片重建走的就是这条）也要带上
        code, fetched = api.handle(f"/api/work_orders/{wo_id}", {})
        self.assertEqual(code, 200)
        assert isinstance(fetched, dict)
        self.assertEqual(fetched["knowledge_id"], closed["knowledge_id"])

    def test_auto_sedimented_entry_is_searchable(self) -> None:
        wo_id = self._new_order("CNC-03", "主轴振动 11.2mm/s 超阈，疑似主轴轴承磨损")
        api.handle(f"/api/work_orders/{wo_id}/close", {}, {"operator": "测试"})
        code, hits = api.handle("/api/knowledge", {"q": "振动", "limit": "50"})
        self.assertEqual(code, 200)
        assert isinstance(hits, list)
        sources = {h["source_wo_id"] for h in hits}
        self.assertIn(wo_id, sources)


if __name__ == "__main__":
    unittest.main(verbosity=2)
