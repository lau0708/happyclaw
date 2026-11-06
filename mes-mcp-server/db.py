"""db.py — 模拟 MES 数据访问层（仅标准库 sqlite3，无第三方依赖）"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent
DATA_DIR = SERVER_ROOT / "data"
DB_PATH = DATA_DIR / "mes.db"

# 振动/温度/压力阈值（模拟，用于巡检异常判定）
THRESHOLDS = {"vibration": 8.0, "temp": 75.0, "pressure": 5.5}


def get_db() -> sqlite3.Connection:
    """获取数据库连接（每行自动按列名返回 dict）"""
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id(prefix: str) -> str:
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
    return f"{prefix}-{ts}"


# --------------------------------------------------------------------------- #
# 查询工具（供 数据Agent 调用）
# --------------------------------------------------------------------------- #
def query_equipment(equipment_id: str | None = None) -> list[dict]:
    """查询设备台账；不传 ID 返回全部设备及其当前状态"""
    with get_db() as conn:
        if equipment_id:
            rows = conn.execute(
                "SELECT * FROM equipment WHERE equipment_id = ?", (equipment_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM equipment").fetchall()
    return [dict(r) for r in rows]


def query_metric(equipment_id: str, hours: int = 24) -> list[dict]:
    """查询设备最近 hours 小时的运行指标"""
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM metric WHERE equipment_id = ? AND ts >= ? ORDER BY ts",
            (equipment_id, since),
        ).fetchall()
    return [dict(r) for r in rows]


def query_history_faults(
    equipment_id: str | None = None, fault_code: str | None = None, limit: int = 5
) -> list[dict]:
    """查询历史故障与处置记录，供同类故障诊断参考"""
    sql = "SELECT * FROM history_fault WHERE 1=1"
    params: list = []
    if equipment_id:
        sql += " AND equipment_id = ?"
        params.append(equipment_id)
    if fault_code:
        sql += " AND fault_code = ?"
        params.append(fault_code)
    sql += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def search_knowledge(symptom_keyword: str, limit: int = 5) -> list[dict]:
    """按症状关键词命中历史沉淀知识（经验回流核心）"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge WHERE symptom LIKE ? OR fault_code LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (f"%{symptom_keyword}%", f"%{symptom_keyword}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# 写操作工具（供 工单Agent / 诊断Agent 调用）
# --------------------------------------------------------------------------- #
def create_work_order(
    equipment_id: str, fault_desc: str, severity: str = "medium"
) -> dict:
    """创建设备异常工单，返回工单号"""
    wo_id = _new_id("WO")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO work_order(wo_id, equipment_id, fault_desc, severity, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (wo_id, equipment_id, fault_desc, severity, "open", now(), now()),
        )
    return {"wo_id": wo_id, "status": "open", "created_at": now()}


def update_work_order(
    wo_id: str, status: str, resolution: str | None = None, operator: str | None = None
) -> dict:
    """更新工单状态 / 回填处置结果"""
    with get_db() as conn:
        conn.execute(
            "UPDATE work_order SET status = ?, updated_at = ? WHERE wo_id = ?",
            (status, now(), wo_id),
        )
        if resolution:
            conn.execute(
                "UPDATE work_order SET plan = ? WHERE wo_id = ?",
                (resolution, wo_id),
            )
        if operator:
            conn.execute(
                "INSERT INTO work_order_log(wo_id, action, detail, operator, ts) "
                "VALUES (?,?,?,?,?)",
                (wo_id, "resolution", resolution or status, operator, now()),
            )
    return get_work_order(wo_id)


def get_work_order(wo_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM work_order WHERE wo_id = ?", (wo_id,)
        ).fetchone()
    return dict(row) if row else None


def query_work_orders(status: str | None = None, equipment_id: str | None = None) -> list[dict]:
    """查询工单列表，可按状态/设备过滤（供看板）"""
    sql = "SELECT * FROM work_order WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if equipment_id:
        sql += " AND equipment_id = ?"
        params.append(equipment_id)
    sql += " ORDER BY created_at DESC"
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def list_knowledge(limit: int = 50) -> list[dict]:
    """列出全部沉淀经验（供看板知识面板）"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def latest_equipment_status() -> list[dict]:
    """汇总每台设备最新指标 + 是否超限 + 未闭环工单数（供看板设备概览）"""
    with get_db() as conn:
        equip_rows = conn.execute("SELECT equipment_id, name, status FROM equipment").fetchall()
        result = []
        for e in equip_rows:
            m = conn.execute(
                "SELECT * FROM metric WHERE equipment_id = ? ORDER BY ts DESC LIMIT 1",
                (e["equipment_id"],),
            ).fetchone()
            open_wo = conn.execute(
                "SELECT COUNT(*) c FROM work_order WHERE equipment_id = ? AND status NOT IN ('done','closed')",
                (e["equipment_id"],),
            ).fetchone()["c"]
            over = {}
            if m:
                over = {k: v for k, v in THRESHOLDS.items() if m[k] and m[k] > v}
            result.append({
                "equipment_id": e["equipment_id"],
                "name": e["name"],
                "status": e["status"],
                "latest_metric": dict(m) if m else None,
                "over_threshold": list(over.keys()),
                "open_work_orders": open_wo,
            })
    return result


def close_work_order(wo_id: str, resolution: str, operator: str = "系统") -> dict | None:
    """把工单置为 closed（看板闭环演示）"""
    with get_db() as conn:
        conn.execute(
            "UPDATE work_order SET status='closed', plan=?, updated_at=? WHERE wo_id=?",
            (resolution, now(), wo_id),
        )
        conn.execute(
            "INSERT INTO work_order_log(wo_id, action, detail, operator, ts) VALUES (?,?,?,?,?)",
            (wo_id, "close", resolution, operator, now()),
        )
    return get_work_order(wo_id)


def save_knowledge(
    fault_code: str,
    symptom: str,
    root_cause: str,
    resolution: str,
    source_wo_id: str | None = None,
) -> dict:
    """沉淀故障处置经验到知识库（复盘环节）"""
    kid = _new_id("KN")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO knowledge(id, fault_code, symptom, root_cause, "
            "resolution, source_wo_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (kid, fault_code, symptom, root_cause, resolution, source_wo_id, now()),
        )
    return {"id": kid, "created_at": now()}


# --------------------------------------------------------------------------- #
# 巡检 / 异常判定
# --------------------------------------------------------------------------- #
def check_latest_metric(equipment_id: str) -> dict | None:
    """读取设备最新一条运行指标，返回是否超阈值及超限项"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM metric WHERE equipment_id = ? ORDER BY ts DESC LIMIT 1",
            (equipment_id,),
        ).fetchone()
    if not row:
        return None
    m = dict(row)
    over = {k: v for k, v in THRESHOLDS.items() if m.get(k, 0) and m[k] > v}
    return {"metric": m, "over_threshold": over}
