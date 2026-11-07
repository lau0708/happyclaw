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
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import feishu  # noqa: E402


class NoConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = (feishu.WEBHOOK_URL, feishu.APP_ID, feishu.APP_SECRET, feishu.CHAT_ID)
        feishu.WEBHOOK_URL = ""
        feishu.APP_ID = feishu.APP_SECRET = feishu.CHAT_ID = ""

    def tearDown(self) -> None:
        feishu.WEBHOOK_URL, feishu.APP_ID, feishu.APP_SECRET, feishu.CHAT_ID = self._orig

    def test_skips_gracefully_without_webhook(self) -> None:
        ok, note = feishu.send_text("test")
        self.assertFalse(ok)
        self.assertIn("未配置", note)

    def test_card_send_skips_without_any_channel(self) -> None:
        ok, note = feishu.send_work_order_card("WO-1", "CNC-03", "root", "fix")
        self.assertFalse(ok)
        self.assertIn("未配置", note)

    def test_app_channel_needs_all_three_settings(self) -> None:
        # 少任何一个都退回 webhook，避免半配置时静默发不出去
        feishu.APP_ID, feishu.APP_SECRET, feishu.CHAT_ID = "id", "sec", ""
        self.assertFalse(feishu.app_bot_enabled())
        feishu.CHAT_ID = "oc_x"
        self.assertTrue(feishu.app_bot_enabled())

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


def _find_buttons(card: dict) -> list[dict]:
    elements = card.get("body", {}).get("elements", [])
    return [e for e in elements if isinstance(e, dict) and e.get("tag") == "button"]


class CardTests(unittest.TestCase):
    def test_card_is_schema_2_with_header_and_body(self) -> None:
        card = feishu.build_work_order_card(
            wo_id="WO-1", equipment="CNC-03 卧式加工中心#3",
            root_cause="主轴轴承磨损", resolution="更换轴承",
            severity="high", confidence=0.85, over_items=["vibration"],
        )
        self.assertEqual(card["schema"], "2.0")
        self.assertTrue(card["config"]["update_multi"])
        self.assertIn("WO-1", card["header"]["title"]["content"])
        self.assertEqual(card["header"]["template"], "red")
        texts = str(card["body"]["elements"])
        for expected in ("CNC-03", "主轴轴承磨损", "0.85", "vibration", "更换轴承"):
            self.assertIn(expected, texts)

    def test_severity_template_color(self) -> None:
        card = feishu.build_work_order_card("WO-2", "E", "r", "f", severity="medium")
        self.assertEqual(card["header"]["template"], "orange")

    def test_open_status_offers_assign_button(self) -> None:
        card = feishu.build_work_order_card("WO-3", "E", "r", "f", status="open")
        buttons = _find_buttons(card)
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["text"]["content"], "接单")
        self.assertEqual(
            buttons[0]["behaviors"][0]["value"],
            {"action": "mes_work_order", "wo_id": "WO-3", "op": "assign"},
        )
        self.assertEqual(buttons[0]["behaviors"][0]["type"], "callback")

    def test_assigned_status_offers_done_button(self) -> None:
        card = feishu.build_work_order_card(
            "WO-4", "E", "r", "f", status="assigned", assignee="王师傅"
        )
        buttons = _find_buttons(card)
        self.assertEqual(buttons[0]["text"]["content"], "已处理")
        self.assertEqual(buttons[0]["behaviors"][0]["value"]["op"], "done")
        self.assertIn("王师傅", str(card["body"]["elements"]))
        self.assertIn("已接单", str(card["body"]["elements"]))

    def test_done_status_offers_close_button(self) -> None:
        card = feishu.build_work_order_card("WO-5", "E", "r", "f", status="done")
        buttons = _find_buttons(card)
        self.assertEqual(buttons[0]["text"]["content"], "确认闭环")
        self.assertEqual(buttons[0]["behaviors"][0]["value"]["op"], "close")

    def test_closed_status_has_no_button(self) -> None:
        card = feishu.build_work_order_card("WO-6", "E", "r", "f", status="closed")
        self.assertEqual(_find_buttons(card), [])


class AppTransportTests(unittest.TestCase):
    """自建应用通道：卡片必须带 Authorization 发到 im/v1/messages"""

    def setUp(self) -> None:
        self._orig = (feishu.APP_ID, feishu.APP_SECRET, feishu.CHAT_ID, feishu.WEBHOOK_URL)
        feishu.APP_ID, feishu.APP_SECRET, feishu.CHAT_ID = "cli_x", "sec_x", "oc_chat"
        feishu.WEBHOOK_URL = ""
        self.calls: list[dict] = []
        self._orig_post = feishu._post

    def tearDown(self) -> None:
        feishu._post = self._orig_post
        feishu.APP_ID, feishu.APP_SECRET, feishu.CHAT_ID, feishu.WEBHOOK_URL = self._orig

    def _fake_post(self, url: str, payload: dict, timeout: int = 10,
                   headers: dict | None = None) -> dict:
        self.calls.append({"url": url, "payload": payload, "headers": headers or {}})
        if "tenant_access_token" in url:
            return {"code": 0, "tenant_access_token": "t-fake"}
        return {"code": 0}

    def test_sends_card_with_token(self) -> None:
        feishu._post = self._fake_post
        ok, note = feishu.send_work_order_card("WO-9", "CNC-03", "r", "f")
        self.assertTrue(ok, note)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("tenant_access_token", self.calls[0]["url"])
        send = self.calls[1]
        self.assertIn("/open-apis/im/v1/messages", send["url"])
        self.assertIn("receive_id_type=chat_id", send["url"])
        self.assertEqual(send["payload"]["receive_id"], "oc_chat")
        self.assertEqual(send["payload"]["msg_type"], "interactive")
        self.assertEqual(send["headers"]["Authorization"], "Bearer t-fake")
        card = json.loads(send["payload"]["content"])
        self.assertEqual(card["schema"], "2.0")
        self.assertTrue(_find_buttons(card), "应用通道发出的卡片必须带可点击按钮")

    def test_provider_error_is_reported_not_raised(self) -> None:
        def failing(url: str, payload: dict, timeout: int = 10,
                    headers: dict | None = None) -> dict:
            if "tenant_access_token" in url:
                return {"code": 0, "tenant_access_token": "t"}
            return {"code": 230001, "msg": "invalid receive_id"}

        feishu._post = failing
        ok, note = feishu.send_work_order_card("WO-10", "E", "r", "f")
        self.assertFalse(ok)
        self.assertIn("230001", note)


class WebhookFallbackTests(unittest.TestCase):
    """只有 webhook 时降级为文本：自定义机器人收不到按钮回调"""

    def setUp(self) -> None:
        self._orig = (feishu.APP_ID, feishu.APP_SECRET, feishu.CHAT_ID, feishu.WEBHOOK_URL)
        feishu.APP_ID = feishu.APP_SECRET = feishu.CHAT_ID = ""
        feishu.WEBHOOK_URL = "https://example.com/hook"

    def tearDown(self) -> None:
        feishu.APP_ID, feishu.APP_SECRET, feishu.CHAT_ID, feishu.WEBHOOK_URL = self._orig

    def test_falls_back_to_text(self) -> None:
        captured: dict = {}
        orig_post = feishu._post

        def fake_post(url: str, payload: dict, timeout: int = 10,
                      headers: dict | None = None) -> dict:
            captured.update(payload=payload)
            return {"code": 0}

        feishu._post = fake_post
        try:
            ok, _ = feishu.send_work_order_card("WO-11", "CNC-03", "轴承磨损", "更换")
        finally:
            feishu._post = orig_post
        self.assertTrue(ok)
        self.assertEqual(captured["payload"]["msg_type"], "text")
        text = captured["payload"]["content"]["text"]
        self.assertIn("WO-11", text)
        self.assertIn("轴承磨损", text)
        self.assertNotIn("behaviors", json.dumps(captured["payload"], ensure_ascii=False))


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
