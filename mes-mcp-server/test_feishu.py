"""test_feishu.py — 飞书推送模块单元测试（不联网）

覆盖：
  - 未配置 webhook：send 跳过且返回说明，不抛异常
  - 签名算法：同输入同输出、base64 格式、随 timestamp 变化
  - 工单卡片结构：header/elements 齐全，severity 决定模板色
  - send_payload 注入 URL 后的 payload 组装（timestamp/sign 字段）

运行：python3 test_feishu.py
"""
from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import feishu  # noqa: E402


class NoConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = feishu.WEBHOOK_URL
        feishu.WEBHOOK_URL = ""

    def tearDown(self) -> None:
        feishu.WEBHOOK_URL = self._orig

    def test_skips_gracefully_without_webhook(self) -> None:
        ok, note = feishu.send_text("test")
        self.assertFalse(ok)
        self.assertIn("未配置", note)

    def test_card_send_skips_without_webhook(self) -> None:
        ok, note = feishu.send_work_order_card("WO-1", "CNC-03", "root", "fix")
        self.assertFalse(ok)
        self.assertIn("未配置", note)

    def test_enabled_flag(self) -> None:
        feishu.WEBHOOK_URL = "https://example.com/hook"
        self.assertTrue(feishu.feishu_enabled())


class SignTests(unittest.TestCase):
    def test_deterministic(self) -> None:
        self.assertEqual(feishu._sign("secret-x", 1700000000),
                         feishu._sign("secret-x", 1700000000))

    def test_changes_with_timestamp_and_secret(self) -> None:
        s1 = feishu._sign("secret-x", 1700000000)
        s2 = feishu._sign("secret-x", 1700000001)
        s3 = feishu._sign("secret-y", 1700000000)
        self.assertNotEqual(s1, s2)
        self.assertNotEqual(s1, s3)

    def test_output_is_base64(self) -> None:
        s = feishu._sign("secret-x", 1700000000)
        base64.b64decode(s)  # 不抛即合法
        self.assertEqual(len(s), 44)  # sha256 摘要 32B → base64 44 字符


class CardTests(unittest.TestCase):
    def test_card_structure(self) -> None:
        card = feishu.build_work_order_card(
            wo_id="WO-1", equipment="CNC-03 卧式加工中心#3",
            root_cause="主轴轴承磨损", resolution="更换轴承",
            severity="high", confidence=0.85, over_items=["vibration"],
        )
        self.assertEqual(card["msg_type"], "interactive")
        self.assertIn("WO-1", card["card"]["header"]["title"]["content"])
        self.assertEqual(card["card"]["header"]["template"], "red")
        texts = str(card["card"]["elements"])
        self.assertIn("CNC-03", texts)
        self.assertIn("主轴轴承磨损", texts)
        self.assertIn("0.85", texts)
        self.assertIn("vibration", texts)

    def test_severity_template_color(self) -> None:
        card = feishu.build_work_order_card("WO-2", "E", "r", "f", severity="medium")
        self.assertEqual(card["card"]["header"]["template"], "orange")


class PayloadAssemblyTests(unittest.TestCase):
    def test_signed_payload_fields(self) -> None:
        self._orig_url, self._orig_secret = feishu.WEBHOOK_URL, feishu.WEBHOOK_SECRET
        feishu.WEBHOOK_URL = "https://example.com/hook"
        feishu.WEBHOOK_SECRET = "sec"
        captured: dict = {}

        def fake_post(url: str, payload: dict, timeout: int = 10) -> dict:
            captured.update(url=url, payload=payload)
            return {"code": 0}

        orig_post = feishu._post
        feishu._post = fake_post
        try:
            ok, note = feishu.send_text("hello")
        finally:
            feishu._post = orig_post
            feishu.WEBHOOK_URL, feishu.WEBHOOK_SECRET = self._orig_url, self._orig_secret
        self.assertTrue(ok)
        self.assertEqual(captured["payload"]["content"]["text"], "hello")
        self.assertIn("timestamp", captured["payload"])
        self.assertIn("sign", captured["payload"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
