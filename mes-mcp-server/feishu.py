"""feishu.py — 飞书消息推送（仅标准库 urllib）

两条通道，按配置自动选择：

1. **自建应用**（推荐，支持卡片按钮回传）：配 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID，
   卡片经 `im/v1/messages` 以应用身份发出。卡片上的「接单 / 已处理 / 确认闭环」按钮
   会回调到 PulseOps（长连接 `card.action.trigger`），由它调 MES API 推进工单状态。
2. **群自定义机器人 Webhook**（最简接入，只能单向推送）：配 FEISHU_WEBHOOK_URL。
   自定义机器人收不到卡片回调，所以这条通道发出去的是**无按钮**的纯文本摘要。

配置（.env 或环境变量）：
  FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID  自建应用三件套，配齐才启用卡片
  FEISHU_WEBHOOK_URL   群机器人 webhook；两条都没配则跳过推送（不阻断巡检）
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

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "")
OPEN_BASE = os.environ.get("FEISHU_OPEN_BASE", "https://open.feishu.cn")

SEVERITY_EMOJI = {"high": "🔴 紧急", "medium": "🟡 一般", "low": "🟢 低优"}
STATUS_LABEL = {
    "open": "待接单",
    "assigned": "已接单",
    "done": "已处理，待闭环",
    "closed": "已闭环",
}
WORK_ORDER_ACTION = "mes_work_order"
# 状态 → 卡片上的下一步按钮（与 db.WORK_ORDER_TRANSITIONS 对齐）
NEXT_ACTION = {
    "open": ("assign", "接单"),
    "assigned": ("done", "已处理"),
    "done": ("close", "确认闭环"),
}


def app_bot_enabled() -> bool:
    """三件套齐了才走应用通道：缺一个就只能退回单向 webhook。"""
    return bool(APP_ID and APP_SECRET and CHAT_ID)


def feishu_enabled() -> bool:
    return bool(WEBHOOK_URL)


def _sign(secret: str, timestamp: int) -> str:
    """飞书自定义机器人签名算法：HMAC-SHA256(key=timestamp\\nsecret) 的 base64"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _post(
    url: str, payload: dict, timeout: int = 10, headers: dict | None = None
) -> dict:
    merged = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=merged,
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


def _tenant_token(timeout: int = 10) -> str:
    """用 App ID/Secret 换 tenant_access_token（应用身份发消息用）"""
    resp = _post(
        f"{OPEN_BASE}/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=timeout,
    )
    if resp.get("code") != 0:
        raise OSError(f"tenant_access_token 获取失败: {resp}")
    return resp["tenant_access_token"]


def send_card_via_app(card: dict) -> tuple[bool, str]:
    """以自建应用身份把卡片发到 FEISHU_CHAT_ID —— 只有这条通道的按钮能回调回来。"""
    try:
        token = _tenant_token()
        resp = _post(
            f"{OPEN_BASE}/open-apis/im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": CHAT_ID,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
            timeout=10,
            headers={"Authorization": f"Bearer {token}"},
        )
    except OSError as e:
        return False, f"应用通道推送失败（不阻断巡检）: {e}"
    if resp.get("code") == 0:
        return True, "已推送（自建应用）"
    return False, f"飞书返回异常: {resp}"


def _card_markdown(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def build_work_order_card(
    wo_id: str,
    equipment: str,
    root_cause: str,
    resolution: str,
    severity: str = "medium",
    confidence: float | None = None,
    over_items: list[str] | None = None,
    status: str = "open",
    assignee: str | None = None,
) -> dict:
    """工单报警卡片（飞书卡片 2.0，带下一步动作按钮）

    卡片里不冗余业务状态：按钮只带 wo_id 与 op，点击后由 PulseOps 调 MES API
    推进状态机，再用返回的最新工单重建卡片。
    """
    sev = SEVERITY_EMOJI.get(severity, severity)
    diag_line = f"**诊断**：{root_cause}"
    if confidence is not None:
        diag_line += f"（置信度 {confidence:.2f}）"

    elements: list[dict] = [_card_markdown(f"**设备**：{equipment}")]
    if over_items:
        elements.append(_card_markdown(f"**超限项**：{', '.join(over_items)}"))
    elements.append(_card_markdown(diag_line))
    elements.append(_card_markdown(f"**处置**：{resolution}"))
    elements.append({"tag": "hr"})
    status_line = f"**状态**：{STATUS_LABEL.get(status, status)}"
    if assignee:
        status_line += f"　**处理人**：{assignee}"
    elements.append(_card_markdown(status_line))

    next_action = NEXT_ACTION.get(status)
    if next_action:
        op, label = next_action
        elements.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": "primary",
                "behaviors": [
                    {
                        "type": "callback",
                        "value": {
                            "action": WORK_ORDER_ACTION,
                            "wo_id": wo_id,
                            "op": op,
                        },
                    }
                ],
            }
        )

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"📋 {sev} | 设备异常工单 {wo_id}",
            },
            "template": "red" if severity == "high" else "orange",
        },
        "body": {"elements": elements},
    }


def card_as_text(
    wo_id: str,
    equipment: str,
    root_cause: str,
    resolution: str,
    severity: str = "medium",
) -> str:
    """webhook 通道的降级文本：自定义机器人收不到按钮回调，就别假装能点。"""
    sev = SEVERITY_EMOJI.get(severity, severity)
    return (
        f"{sev} 设备异常工单 {wo_id}\n"
        f"设备：{equipment}\n"
        f"诊断：{root_cause}\n"
        f"处置：{resolution}\n"
        f"（请在设备看板接单与回填，或配置自建应用启用卡片按钮）"
    )


def send_work_order_card(
    wo_id: str,
    equipment: str,
    root_cause: str,
    resolution: str,
    severity: str = "medium",
    confidence: float | None = None,
    over_items: list[str] | None = None,
    status: str = "open",
    assignee: str | None = None,
) -> tuple[bool, str]:
    """推送工单报警卡片：优先自建应用（按钮可回写），否则退回 webhook 文本。"""
    if app_bot_enabled():
        return send_card_via_app(
            build_work_order_card(
                wo_id,
                equipment,
                root_cause,
                resolution,
                severity=severity,
                confidence=confidence,
                over_items=over_items,
                status=status,
                assignee=assignee,
            )
        )
    if not WEBHOOK_URL:
        return False, "未配置 FEISHU_APP_ID/CHAT_ID 与 FEISHU_WEBHOOK_URL，跳过推送"
    return send_text(
        card_as_text(wo_id, equipment, root_cause, resolution, severity=severity)
    )
