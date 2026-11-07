"""test_state_machine.py — 工单状态机 + 数据口径回归测试（仅标准库 unittest）

覆盖交付标准验收判据：
  - 非法流转被拒绝（跳级 / 回退 / 非法状态 / 终态再流转）
  - assigned 必须携带 operator
  - 合法路径（open→assigned→done→closed、open→done）正常流转
  - 看板一键关单沿合法路径补全并逐步留痕，closed 幂等
  - query_history_faults 兼容 fault_code 与 fault_id 两种口径
  - 阈值判异边界值（等于阈值不判超限）

运行：python3 init_db.py && python3 test_state_machine.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import init_db  # noqa: E402


class WorkOrderStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        init_db.main()  # 每个用例前重建种子数据

    def _new_order(self) -> str:
        return db.create_work_order("CNC-03", "测试工单", severity="high")["wo_id"]

    def test_legal_full_path(self) -> None:
        wo_id = self._new_order()
        db.update_work_order(wo_id, status="assigned", operator="维修工-张工")
        self.assertEqual(db.get_work_order(wo_id)["assignee"], "维修工-张工")
        db.update_work_order(wo_id, status="done", resolution="已修复", operator="维修工-张工")
        db.update_work_order(wo_id, status="closed")
        self.assertEqual(db.get_work_order(wo_id)["status"], "closed")

    def test_legal_short_path_open_to_done(self) -> None:
        wo_id = self._new_order()
        db.update_work_order(wo_id, status="done", resolution="直接回填", operator="张工")
        self.assertEqual(db.get_work_order(wo_id)["status"], "done")

    def test_skip_level_rejected(self) -> None:
        wo_id = self._new_order()
        with self.assertRaises(db.WorkOrderError):
            db.update_work_order(wo_id, status="closed")

    def test_rollback_rejected(self) -> None:
        wo_id = self._new_order()
        db.update_work_order(wo_id, status="done", operator="张工")
        with self.assertRaises(db.WorkOrderError):
            db.update_work_order(wo_id, status="open")

    def test_invalid_status_rejected(self) -> None:
        wo_id = self._new_order()
        with self.assertRaises(db.WorkOrderError):
            db.update_work_order(wo_id, status="finished")

    def test_terminal_state_frozen(self) -> None:
        wo_id = self._new_order()
        db.update_work_order(wo_id, status="done", operator="张工")
        db.update_work_order(wo_id, status="closed")
        with self.assertRaises(db.WorkOrderError):
            db.update_work_order(wo_id, status="open")

    def test_assigned_requires_operator(self) -> None:
        wo_id = self._new_order()
        with self.assertRaises(db.WorkOrderError):
            db.update_work_order(wo_id, status="assigned")

    def test_nonexistent_order_rejected(self) -> None:
        with self.assertRaises(db.WorkOrderError):
            db.update_work_order("WO-NOT-EXIST", status="done")

    def test_board_close_walks_legal_path_and_logs(self) -> None:
        wo_id = self._new_order()
        closed = db.close_work_order(wo_id, "人工回填闭环", operator="看板")
        self.assertEqual(closed["status"], "closed")
        with db.get_db() as conn:
            logs = conn.execute(
                "SELECT action, detail FROM work_order_log WHERE wo_id = ? ORDER BY ts",
                (wo_id,),
            ).fetchall()
        details = [r["detail"] for r in logs]
        actions = [r["action"] for r in logs]
        self.assertIn("open → assigned", details)
        self.assertIn("assigned → done", details)
        self.assertIn("done → closed", details)
        # 3 次状态流转日志 + 1 条关单处置(resolution)日志
        flow = [a for a in actions if a != "knowledge_sediment"]
        self.assertEqual(len(flow), 4)
        # 闭环会顺手沉淀一条经验并留痕（内容质量由 test_sediment 覆盖）
        self.assertIn("knowledge_sediment", actions)

    def test_board_close_idempotent(self) -> None:
        wo_id = self._new_order()
        db.close_work_order(wo_id, "人工回填闭环")
        again = db.close_work_order(wo_id, "重复点击")
        self.assertEqual(again["status"], "closed")

    def test_board_close_nonexistent_rejected(self) -> None:
        with self.assertRaises(db.WorkOrderError):
            db.close_work_order("WO-NOT-EXIST", "x")


class DataContractTests(unittest.TestCase):
    def setUp(self) -> None:
        init_db.main()

    def test_history_fault_by_code(self) -> None:
        rows = db.query_history_faults(fault_code="SPINDLE-BEARING")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fault_id"], "F-001")

    def test_history_fault_by_id(self) -> None:
        """TC-B7：fault_code 参数传主键 F-001 也能命中"""
        rows = db.query_history_faults(fault_code="F-001")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fault_code"], "SPINDLE-BEARING")

    def test_threshold_boundary_not_over(self) -> None:
        """边界值：恰好等于阈值不算超限（严格大于才判异）"""
        wo = db.create_work_order("CNC-01", "边界测试")
        del wo
        m = db.check_latest_metric("CNC-03")
        self.assertTrue(m["over_threshold"])  # 种子数据 CNC-03 振动超标
        self.assertNotIn("vibration", db.check_latest_metric("CNC-01")["over_threshold"])

    def test_equipment_status_board_fields(self) -> None:
        rows = db.latest_equipment_status()
        target = next(r for r in rows if r["equipment_id"] == "CNC-03")
        self.assertIn("over_threshold", target)
        self.assertIn("open_work_orders", target)


if __name__ == "__main__":
    unittest.main(verbosity=2)
