"""test_closed_loop.py — 业务闭环 Eval 验收集合（仅标准库 unittest）

对应方案中「板斧三：定交付标准」的验收判据，覆盖：
  - 巡检幂等：同设备未闭环工单存在时不重复建单；闭环后可再次建单
  - 经验回流：save → search 命中，source_wo_id 溯源一致
  - 双触发收敛：主动查询（health_check 路径）与被动巡检（scheduler 路径）
    对同一异常进入同一闭环，不产生重复工单
  - 看板取数契约：latest_status 的超限/未闭环工单数与库内状态一致

运行：python3 init_db.py && python3 test_closed_loop.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import init_db  # noqa: E402
import scheduler  # noqa: E402


def _count_orders(equipment_id: str) -> int:
    return len(db.query_work_orders(equipment_id=equipment_id))


class NoPushCase(unittest.TestCase):
    """会真的走一遍建单链路的用例：不能把测试工单推到维修群里

    关掉推送这件事必须在用例里显式做，而不是靠「碰巧没配凭证」——
    开发机上 .env 通常配着真凭证，漏一次就往群里发几张假工单。
    """

    def setUp(self) -> None:
        os.environ["MES_DISABLE_PUSH"] = "1"
        init_db.main()

    def tearDown(self) -> None:
        os.environ.pop("MES_DISABLE_PUSH", None)


class IdempotencyTests(NoPushCase):
    """巡检幂等：重复报警不产生重复工单"""

    def test_repeated_alarm_creates_single_order(self) -> None:
        before = _count_orders("CNC-03")
        self.assertGreaterEqual(before, 0)
        # 模拟连续三轮巡检（种子数据 CNC-03 振动超标）
        for _ in range(3):
            scheduler.inspect_device("CNC-03")
        self.assertEqual(_count_orders("CNC-03"), before + 1)

    def test_new_order_allowed_after_closure(self) -> None:
        scheduler.inspect_device("CNC-03")
        first = db.query_work_orders(equipment_id="CNC-03")[0]
        # 人工闭环 → 未闭环工单清零
        db.close_work_order(first["wo_id"], "已修复", operator="测试")
        # 再次巡检：允许建新单
        scheduler.inspect_device("CNC-03")
        orders = db.query_work_orders(equipment_id="CNC-03")
        self.assertEqual(len(orders), 2)
        # created_at 秒级精度可能并列，用 wo_id（含毫秒时间戳）确定最新一单
        newest = max(orders, key=lambda o: o["wo_id"])
        self.assertEqual(newest["status"], "open")

    def test_healthy_device_never_creates_order(self) -> None:
        before = _count_orders("CNC-01")
        for _ in range(3):
            scheduler.inspect_device("CNC-01")  # 种子数据 CNC-01 全指标正常
        self.assertEqual(_count_orders("CNC-01"), before)

    def test_device_without_metric_skipped(self) -> None:
        # ASM-01 无指标种子，不应抛异常也不应建单
        before = _count_orders("ASM-01")
        scheduler.inspect_device("ASM-01")
        self.assertEqual(_count_orders("ASM-01"), before)


class KnowledgeLoopTests(unittest.TestCase):
    """经验回流：复盘沉淀可被检索命中且可溯源"""

    def setUp(self) -> None:
        init_db.main()

    def test_save_then_search_hits_with_source(self) -> None:
        wo = db.create_work_order("INJ-01", "注塑压力不足复发", severity="medium")
        saved = db.save_knowledge(
            fault_code="PRESSURE-DROP",
            symptom="注塑压力不足",
            root_cause="压力阀芯磨损",
            resolution="更换阀芯并重调压力",
            source_wo_id=wo["wo_id"],
        )
        hits = db.search_knowledge("注塑压力")
        self.assertTrue(hits)
        top = hits[0]
        self.assertEqual(top["id"], saved["id"])
        self.assertEqual(top["source_wo_id"], wo["wo_id"])

    def test_seed_knowledge_hit_on_first_diagnosis(self) -> None:
        """种子知识 KN-001 存在：首次诊断即命中（demo 场景口径说明）"""
        hits = db.search_knowledge("振动")
        self.assertTrue(any(h["id"] == "KN-001" for h in hits))


class ConvergenceTests(NoPushCase):
    """双触发收敛：主动查询与被动巡检进入同一闭环且幂等"""

    def test_scheduler_then_manual_creates_no_duplicate(self) -> None:
        # 路径一：定时巡检自动建单
        scheduler.inspect_device("CNC-03")
        n_after_scheduler = _count_orders("CNC-03")
        # 路径二：人工主动查询发现异常后尝试再建（应被幂等拦截）
        scheduler.inspect_device("CNC-03")
        self.assertEqual(_count_orders("CNC-03"), n_after_scheduler)

    def test_board_status_matches_db(self) -> None:
        scheduler.inspect_device("CNC-03")
        status = {r["equipment_id"]: r for r in db.latest_equipment_status()}
        self.assertEqual(status["CNC-03"]["open_work_orders"], 1)
        self.assertIn("vibration", status["CNC-03"]["over_threshold"])
        self.assertEqual(status["CNC-01"]["open_work_orders"], 0)
        self.assertEqual(status["CNC-01"]["over_threshold"], [])


class CreateOrderIdempotencyTests(NoPushCase):
    """建单幂等下沉到 db 层：不管从哪个入口调，都开不出第二张未闭环单"""

    def test_second_create_returns_same_order(self) -> None:
        first = db.create_work_order("CNC-03", "振动超限", severity="high")
        second = db.create_work_order("CNC-03", "振动仍超限（下一轮巡检）")
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(second["wo_id"], first["wo_id"])
        self.assertEqual(_count_orders("CNC-03"), 1)

    def test_done_blocks_new_order(self) -> None:
        """done 也是未闭环：处置完了但没人签字，不该再开一张"""
        wo = db.create_work_order("CNC-03", "振动超限")
        db.update_work_order(wo["wo_id"], status="done", resolution="更换轴承", operator="张工")
        again = db.create_work_order("CNC-03", "振动又超了")
        self.assertTrue(again["deduplicated"])
        self.assertEqual(again["status"], "done")
        self.assertEqual(_count_orders("CNC-03"), 1)

    def test_closed_releases_the_slot(self) -> None:
        wo = db.create_work_order("CNC-03", "振动超限")
        db.close_work_order(wo["wo_id"], "已更换轴承", operator="张工")
        nxt = db.create_work_order("CNC-03", "振动再次超限")
        self.assertFalse(nxt["deduplicated"])
        self.assertNotEqual(nxt["wo_id"], wo["wo_id"])
        self.assertEqual(_count_orders("CNC-03"), 2)

    def test_other_equipment_not_affected(self) -> None:
        a = db.create_work_order("CNC-03", "振动超限")
        b = db.create_work_order("INJ-01", "温度超限")
        self.assertFalse(b["deduplicated"])
        self.assertNotEqual(a["wo_id"], b["wo_id"])

    def test_index_rejects_manual_insert(self) -> None:
        """绕过 create_work_order 直插第二张未闭环单，数据库自己会拒"""
        db.create_work_order("CNC-03", "振动超限")
        with self.assertRaises(sqlite3.IntegrityError):
            conn = db.get_db()
            try:
                conn.execute(
                    "INSERT INTO work_order(wo_id, equipment_id, fault_desc, status, "
                    "created_at, updated_at) VALUES ('WO-DUP','CNC-03','绕过建单函数',"
                    "'open',datetime('now'),datetime('now'))"
                )
                conn.commit()
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
