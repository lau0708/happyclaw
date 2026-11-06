"""feishu.py — 飞书消息推送（仅标准库 urllib）

支持飞书群自定义机器人 Webhook（最简接入：群设置 → 群机器人 → 添加自定义机器人，
拿到 webhook URL 填入 .env 即可）。可选开启签名校验（机器人安全设置选「签名校验」时填 secret）。

配置（.env 或环境变量）：
  FEISHU_WEBHOOK_URL   必填才启用推送；为空则跳过（不阻断巡检）
  FEISHU_WEBHOOK_SECRET 可选，机器人开启签名校验时必填

用法：
  from feishu import send_work_order_card, send_text
  send_work_order_card(wo_id="WO-xxx", equipment="CNC-03 卧式加工中心#3",
                       root_cause="主轴轴承磨损", resolution="更换轴承…",
                       severity="high", confidence=0.85)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """加载同目录 .env（与 agent_nodes 同款逻辑，环境变量优先）"""
    env_path = SERVER_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("FEISHU_WEBHOOK_SECRET", "")

SEVERITY_EMOJI = {"high": "🔴 紧急", "medium": "🟡 一般", "low": "🟢 低优"}


def feishu_enabled() -> bool:
    return bool(WEBHOOK_URL)


def _sign(secret: str, timestamp: int) -> str:
    """飞书自定义机器人签名算法：HMAC-SHA256(key=timestamp\\nsecret) 的 base64"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _post(url: str, payload: dict, timeout: int = 10) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_payload(payload: dict) -> tuple[bool, str]:
    """底层发送：返回 (成功?, 说明)。任何异常吞掉返回 False，绝不阻断业务链路。"""
    if not WEBHOOK_URL:
        return False, "未配置 FEISHU_WEBHOOK_URL，跳过推送"
    try:
        if WEBHOOK_SECRET:
            ts = int(time.time())
            payload["timestamp"] = str(ts)
            payload["sign"] = _sign(WEBHOOK_SECRET, ts)
        resp = _post(WEBHOOK_URL, payload)
        if resp.get("code") == 0 or resp.get("StatusCode") == 0:
            return True, "已推送"
        return False, f"飞书返回异常: {resp}"
    except OSError as e:
        return False, f"推送失败（不阻断巡检）: {e}"


def send_text(text: str) -> tuple[bool, str]:
    return send_payload({"msg_type": "text", "content": {"text": text}})


def build_work_order_card(
    wo_id: str,
    equipment: str,
    root_cause: str,
    resolution: str,
    severity: str = "medium",
    confidence: float | None = None,
    over_items: list[str] | None = None,
) -> dict:
    """工单报警卡片消息（Interactive Card）"""
    sev = SEVERITY_EMOJI.get(severity, severity)
    diag_line = f"诊断：{root_cause}"
    if confidence is not None:
        diag_line += f"（置信度 {confidence:.2f}）"
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": diag_line}},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**处置方案**：{resolution}"}},
        {"tag": "hr"},
        {"tag": "note", "elements": [{"tag": "plain_text",
                                      "content": f"工单 {wo_id} · 设备异常工单闭环系统 · 请尽快接单处置"}]},
    ]
    if over_items:
        elements.insert(0, {"tag": "div", "text": {"tag": "lark_md",
                                                   "content": f"超限项：{', '.join(over_items)}"}})
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📋 {sev} | 设备异常工单 {wo_id}"},
                "template": "red" if severity == "high" else "orange",
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**设备**：{equipment}"}},
                *elements,
            ],
        },
    }


def send_work_order_card(
    wo_id: str,
    equipment: str,
    root_cause: str,
    resolution: str,
    severity: str = "medium",
    confidence: float | None = None,
    over_items: list[str] | None = None,
) -> tuple[bool, str]:
    """推送工单报警卡片到飞书群"""
    payload = build_work_order_card(
        wo_id, equipment, root_cause, resolution,
        severity=severity, confidence=confidence, over_items=over_items,
    )
    return send_payload(payload)
