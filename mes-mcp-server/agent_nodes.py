"""agent_nodes.py — agentic workflow 的 LLM 节点（仅标准库，OpenAI 兼容协议）

设计原则（对应方案「板斧二」）：
  确定性骨架（巡检/阈值/幂等/建单/状态机）保持普通代码，
  只在两个节点引入模型：
    ① 诊断节点 diagnose()：输入结构化事实包，输出根因假设 + 处置方案 + 置信度
    ② 问诊解析节点 parse_inquiry()：把自然语言问询解析成 equipment_id + 意图

约束：
  - 模型只做「判断与生成」，所有数字来自工具返回的事实包，不允许模型编造
  - 未配置 LLM_API_KEY 时自动降级为知识库关键词命中（零依赖可跑通全链路）
  - 响应强制 JSON；解析失败重试一次后降级，不让坏输出污染工单

配置（环境变量）：
  LLM_BASE_URL  默认 https://api.deepseek.com/v1（任何 OpenAI 兼容端点均可）
  LLM_API_KEY   必填才启用真实模型；为空则降级
  LLM_MODEL     默认 deepseek-chat
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable

import db

# --------------------------------------------------------------------------- #
# 配置与传输层（可注入，便于单测）
# --------------------------------------------------------------------------- #
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

Transport = Callable[[str, dict], dict]  # (url, payload) -> response dict


def _http_transport(url: str, payload: dict) -> dict:
    """OpenAI 兼容 /chat/completions 调用（stdlib urllib，超时 30s）"""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def llm_enabled() -> bool:
    return bool(LLM_API_KEY)


def _chat(transport: Transport, system: str, user: str) -> str:
    resp = transport(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
    )
    return resp["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------- #
# ① 诊断节点
# --------------------------------------------------------------------------- #
DIAGNOSE_SYSTEM = """你是设备维修诊断专家。只依据「事实包」中的数据做诊断，
不得编造任何数值。输出严格 JSON，字段：
  root_cause   根因假设（一句话）
  resolution   处置方案（一句话，可执行）
  confidence   置信度 0~1（事实包含知识库命中时才允许 >0.8）
  reasoning    依据（引用事实包中的具体数据点）"""


def build_fact_pack(equipment_id: str) -> dict:
    """结构化事实包：只喂模型需要的数据（控制 token，数字全部可对账）"""
    equips = db.query_equipment(equipment_id)
    latest = db.check_latest_metric(equipment_id) or {}
    faults = db.query_history_faults(equipment_id, limit=3)
    knowledge = []
    if latest.get("over_threshold"):
        keyword = "振动" if "vibration" in latest["over_threshold"] else "温度"
        knowledge = db.search_knowledge(keyword, limit=3)
    return {
        "equipment": equips[0] if equips else None,
        "latest_metric": latest.get("metric"),
        "over_threshold": latest.get("over_threshold", {}),
        "history_faults": faults,
        "knowledge_hits": knowledge,
    }


def _fallback_diagnose(fact_pack: dict) -> dict:
    """降级路径：知识库关键词命中（与原 scheduler.py 逻辑等价）"""
    hits = fact_pack.get("knowledge_hits") or []
    if hits:
        return {
            "root_cause": hits[0]["root_cause"],
            "resolution": hits[0]["resolution"],
            "confidence": 0.8,
            "reasoning": f"知识库命中 {hits[0]['id']}（未配置 LLM，关键词命中降级）",
        }
    over = list((fact_pack.get("over_threshold") or {}).keys())
    return {
        "root_cause": "待现场排查",
        "resolution": "停机检查并定位故障",
        "confidence": 0.3,
        "reasoning": f"超限项 {over}，无知识命中且未配置 LLM",
    }


def _parse_plan(raw: str) -> dict | None:
    try:
        plan = json.loads(raw)
        if not all(k in plan for k in ("root_cause", "resolution", "confidence")):
            return None
        plan["confidence"] = float(plan["confidence"])
        return plan
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def diagnose(equipment_id: str, transport: Transport | None = None) -> dict:
    """诊断节点：事实包 → 根因假设 + 处置方案 + 置信度。

    LLM 可用时走模型（解析失败重试 1 次），否则降级知识库命中。
    返回 plan 附带 fact_pack（供工单描述与审计）。
    """
    fact_pack = build_fact_pack(equipment_id)
    if not fact_pack["latest_metric"]:
        plan = _fallback_diagnose(fact_pack)
        plan["fact_pack"] = fact_pack
        return plan

    if llm_enabled():
        transport = transport or _http_transport
        user = json.dumps(fact_pack, ensure_ascii=False)
        raw = _chat(transport, DIAGNOSE_SYSTEM, user)
        plan = _parse_plan(raw)
        if plan is None:  # 重试一次
            raw = _chat(transport, DIAGNOSE_SYSTEM, user + "\n注意：上一次输出不是合法 JSON。")
            plan = _parse_plan(raw)
        if plan is not None:
            plan["fact_pack"] = fact_pack
            return plan
        # 模型输出两次都不合法 → 降级，不让坏输出进工单

    plan = _fallback_diagnose(fact_pack)
    plan["fact_pack"] = fact_pack
    return plan


# --------------------------------------------------------------------------- #
# ② 问诊解析节点
# --------------------------------------------------------------------------- #
PARSE_SYSTEM = """把操作工的自然语言问询解析为结构化意图。输出严格 JSON：
  equipment_id  设备编号（如 CNC-03）；问询无法定位设备时为 null
  intent        意图：query（查询健康状态）/ report（报告异常，需建单）
  symptom       症状关键词（供知识库检索，可为空字符串）"""


def parse_inquiry(text: str, equipment_ids: list[str], transport: Transport | None = None) -> dict:
    """把「3号机振动不对，看下」解析成 equipment_id + intent，路由进闭环。"""
    if llm_enabled():
        transport = transport or _http_transport
        user = f"设备清单: {equipment_ids}\n问询: {text}"
        raw = _chat(transport, PARSE_SYSTEM, user)
        result = _parse_plan(raw)  # 同样的 JSON 宽松解析
        if result is not None:
            if result.get("equipment_id") not in equipment_ids:
                result["equipment_id"] = None
            result["confidence"] = result.get("confidence", 1.0)
            return result
    # 降级：规则解析（设备号直接匹配 + 异常词判断）
    lower = text.lower()
    equip = next((e for e in equipment_ids if e.lower() in lower), None)
    intent = "report" if any(w in text for w in ("异常", "不对", "报警", "故障", "响")) else "query"
    return {"equipment_id": equip, "intent": intent, "symptom": text, "confidence": 0.5}
