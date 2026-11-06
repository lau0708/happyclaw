"""test_agent_nodes.py — LLM 节点单元测试（假传输层，不联网、不消耗 token）

覆盖：
  - 未配置 key：降级为知识库命中，链路可用
  - 配置 key + 合法 JSON 响应：采用模型输出
  - 模型输出非法 JSON 两次：自动降级，坏输出不进工单
  - 事实包字段完整、数字可对账（来自 db，不含模型编造）
  - 问诊解析：合法解析采用；设备号不在清单内被纠正为 None；无 key 走规则解析

运行：python3 init_db.py && python3 test_agent_nodes.py
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_nodes  # noqa: E402
import db  # noqa: E402
import init_db  # noqa: E402


def _fake_llm(content: str) -> agent_nodes.Transport:
    def transport(url: str, payload: dict) -> dict:
        return {"choices": [{"message": {"content": content}}]}

    return transport


def _model_plan() -> str:
    return json.dumps({
        "root_cause": "主轴轴承磨损加剧",
        "resolution": "更换主轴轴承并做动平衡校准",
        "confidence": 0.85,
        "reasoning": "最新振动 12.8mm/s 超阈值 8.0，命中历史故障 SPINDLE-BEARING",
    })


class DiagnoseTests(unittest.TestCase):
    def setUp(self) -> None:
        init_db.main()
        self._orig_key = agent_nodes.LLM_API_KEY
        agent_nodes.LLM_API_KEY = ""  # 默认降级

    def tearDown(self) -> None:
        agent_nodes.LLM_API_KEY = self._orig_key

    def test_fallback_without_key_uses_knowledge(self) -> None:
        plan = agent_nodes.diagnose("CNC-03")
        self.assertEqual(plan["root_cause"], "主轴轴承磨损")  # 种子知识 KN-001
        self.assertGreaterEqual(plan["confidence"], 0.8)
        self.assertIn("fact_pack", plan)

    def test_fallback_without_knowledge_hit(self) -> None:
        plan = agent_nodes.diagnose("ASM-01")  # 无指标、无命中
        self.assertEqual(plan["root_cause"], "待现场排查")

    def test_model_output_used_when_valid(self) -> None:
        agent_nodes.LLM_API_KEY = "test-key"
        plan = agent_nodes.diagnose("CNC-03", transport=_fake_llm(_model_plan()))
        self.assertEqual(plan["root_cause"], "主轴轴承磨损加剧")
        self.assertEqual(plan["confidence"], 0.85)

    def test_bad_json_twice_falls_back(self) -> None:
        agent_nodes.LLM_API_KEY = "test-key"
        calls: list[int] = []

        def flaky(url: str, payload: dict) -> dict:
            calls.append(1)
            return {"choices": [{"message": {"content": "抱歉，我无法输出 JSON"}}]}

        plan = agent_nodes.diagnose("CNC-03", transport=flaky)
        self.assertEqual(len(calls), 2)  # 恰好重试一次
        self.assertEqual(plan["root_cause"], "主轴轴承磨损")  # 降级到知识库命中

    def test_fact_pack_numbers_traceable(self) -> None:
        plan = agent_nodes.diagnose("CNC-03")
        fp = plan["fact_pack"]
        latest = db.check_latest_metric("CNC-03")
        self.assertEqual(fp["latest_metric"], latest["metric"])
        self.assertEqual(
            fp["over_threshold"], {k: v for k, v in latest["over_threshold"].items()}
        )


class ParseInquiryTests(unittest.TestCase):
    def setUp(self) -> None:
        init_db.main()
        self._orig_key = agent_nodes.LLM_API_KEY
        self._ids = [e["equipment_id"] for e in db.query_equipment()]

    def tearDown(self) -> None:
        agent_nodes.LLM_API_KEY = self._orig_key

    def test_llm_parse_normalized(self) -> None:
        agent_nodes.LLM_API_KEY = "test-key"
        raw = json.dumps({"equipment_id": "CNC-99", "intent": "query", "symptom": "振动"})
        result = agent_nodes.parse_inquiry("3号机怎么样", self._ids, transport=_fake_llm(raw))
        # 模型幻觉出的设备号不在清单内 → 纠正为 None
        self.assertIsNone(result["equipment_id"])

    def test_rule_fallback_without_key(self) -> None:
        result = agent_nodes.parse_inquiry("CNC-03 振动不对，看下", self._ids)
        self.assertEqual(result["equipment_id"], "CNC-03")
        self.assertEqual(result["intent"], "report")
        self.assertLessEqual(result["confidence"], 0.6)

    def test_rule_fallback_query_intent(self) -> None:
        result = agent_nodes.parse_inquiry("查一下 CNC-01 的状态", self._ids)
        self.assertEqual(result["equipment_id"], "CNC-01")
        self.assertEqual(result["intent"], "query")


if __name__ == "__main__":
    unittest.main(verbosity=2)
