"""db.py — MES 数据访问层（仅标准库 sqlite3，无第三方依赖）

两类写入路径：
  1. 人工/巡检业务写入：工单、知识库（create_work_order / save_knowledge …）
  2. 设备采集写入：insert_metric（网关 HTTP 上报 → 本函数），带物理量校验与幂等
"""
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

# 采集上报的物理合理区间：超出即视为脏数据 / 单位错误，直接拒绝
# 宁可丢一条脏点，也不能让它写进时序后误触发一张工单
METRIC_RANGES: dict[str, tuple[float, float]] = {
    "temp": (-40.0, 500.0),        # ℃
    "vibration": (0.0, 200.0),     # mm/s（振动烈度）
    "pressure": (0.0, 100.0),      # MPa
    "rpm": (0.0, 60_000.0),        # r/min
    "run_hours": (0.0, 10_000_000.0),
}
METRIC_FIELDS = ("temp", "vibration", "pressure", "rpm", "run_hours")


def load_env(path: Path | None = None) -> None:
    """加载同目录 .env（不覆盖已有环境变量），供 api / simulator 复用"""
    env_path = path or (SERVER_ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# --------------------------------------------------------------------------- #
# 工单状态机：合法流转表（跳级 / 回退 / 终态再流转一律拒绝）
#   open → assigned → done → closed
#   open → done 允许（无派单环节、直接回填处置结果的场景）
# --------------------------------------------------------------------------- #
WORK_ORDER_STATUSES = ("open", "assigned", "done", "closed")
WORK_ORDER_TRANSITIONS: dict[str, set[str]] = {
    "open": {"assigned", "done"},
    "assigned": {"done"},
    "done": {"closed"},
    "closed": set(),  # 终态
}


class WorkOrderError(ValueError):
    """工单状态机校验失败（非法状态 / 非法流转 / 工单不存在）"""


class MetricError(ValueError):
    """指标上报校验失败（数值非法 / 时间格式错误 / 无有效指标）"""


class EquipmentNotFound(MetricError):
    """上报的 equipment_id 不在设备台账中（编码映射没对齐）"""


def get_db() -> sqlite3.Connection:
    """获取数据库连接（每行自动按列名返回 dict）"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except FileExistsError:  # 部分环境的 mkdir 代理在目录已存在时仍抛出，忽略即可
        pass
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
        # 兼容两种口径：fault_code（如 SPINDLE-BEARING）或 fault_id 主键（如 F-001）
        sql += " AND (fault_code = ? OR fault_id = ?)"
        params.extend([fault_code, fault_code])
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
    wo_id: str,
    status: str | None = None,
    resolution: str | None = None,
    operator: str | None = None,
) -> dict:
    """更新工单状态 / 回填处置结果（经状态机校验，非法流转抛 WorkOrderError）

    约束：
      - 工单必须存在，状态必须属于 WORK_ORDER_STATUSES
      - 状态流转必须符合 WORK_ORDER_TRANSITIONS（不跳级、不回退）
      - 流转到 assigned 必须提供 operator（派单对象），同时回填 assignee
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM work_order WHERE wo_id = ?", (wo_id,)
        ).fetchone()
        if row is None:
            raise WorkOrderError(f"工单不存在: {wo_id}")
        current = row["status"]

        if status is not None and status != current:
            if status not in WORK_ORDER_TRANSITIONS:
                raise WorkOrderError(
                    f"非法状态: {status}（合法状态: {'/'.join(WORK_ORDER_STATUSES)}）"
                )
            if status not in WORK_ORDER_TRANSITIONS[current]:
                raise WorkOrderError(
                    f"非法流转: {current} → {status}"
                    f"（允许: {'/'.join(sorted(WORK_ORDER_TRANSITIONS[current])) or '无，已是终态'}）"
                )
            if status == "assigned" and not (operator or row["assignee"]):
                raise WorkOrderError("流转到 assigned 必须提供 operator（派单对象）")
            conn.execute(
                "UPDATE work_order SET status = ?, updated_at = ? WHERE wo_id = ?",
                (status, now(), wo_id),
            )
            if status == "assigned" and operator:
                conn.execute(
                    "UPDATE work_order SET assignee = ? WHERE wo_id = ?",
                    (operator, wo_id),
                )
            conn.execute(
                "INSERT INTO work_order_log(wo_id, action, detail, operator, ts) "
                "VALUES (?,?,?,?,?)",
                (wo_id, "status_change", f"{current} → {status}", operator, now()),
            )

        if resolution:
            conn.execute(
                "UPDATE work_order SET plan = ?, updated_at = ? WHERE wo_id = ?",
                (resolution, now(), wo_id),
            )
            if operator:
                conn.execute(
                    "INSERT INTO work_order_log(wo_id, action, detail, operator, ts) "
                    "VALUES (?,?,?,?,?)",
                    (wo_id, "resolution", resolution, operator, now()),
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
                "SELECT * FROM metric WHERE equipment_id = ? ORDER BY ts DESC, rowid DESC LIMIT 1",
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
    """看板人工回填闭环：沿状态机从当前状态合法推进到 closed，全程留痕

    不再直接 UPDATE status='closed' 绕过状态机，而是把缺失的中间环节
    （assigned / done）按合法流转逐步补全，每步写 work_order_log；
    已是 closed 的工单幂等返回，不存在的工单抛 WorkOrderError。

    闭环成功即触发一次经验沉淀（见 sediment_knowledge_from_work_order）：
    它自己幂等，所以重复关单不会沉淀出第二条；沉淀失败只记日志，不影响关单。
    """
    wo = get_work_order(wo_id)
    if wo is None:
        raise WorkOrderError(f"工单不存在: {wo_id}")
    if wo["status"] == "closed":
        # 幂等返回。若上一次沉淀因故没写成，这里补一次 —— 沉淀本身是幂等的。
        _try_sediment_quietly(wo_id)
        return wo

    # 从当前状态到 closed 的最短合法路径
    path_map = {
        "open": ["assigned", "done", "closed"],
        "assigned": ["done", "closed"],
        "done": ["closed"],
    }
    for step in path_map[wo["status"]]:
        update_work_order(
            wo_id,
            status=step,
            # 只有调用方真的给了处置结果才覆盖 plan。空留白必须保持原样：
            # 关单这一步的默认文案一旦写进 plan，会把 done 环节回填的真实
            # 处置方案盖掉，沉淀出来的经验就成了「人工回填闭环」这种废话。
            resolution=(resolution or None) if step == "closed" else None,
            operator=operator,
        )
    _try_sediment_quietly(wo_id)
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
# 经验回流：工单闭环 → 知识库自动沉淀
# --------------------------------------------------------------------------- #
# 症状关键词 → 故障码。顺序即优先级：越具体的排越前，否则「主轴轴承磨损」
# 会被「振动」抢走，沉淀出来的码就没法指导下一次诊断。
FAULT_CODE_HINTS: tuple[tuple[str, str], ...] = (
    ("轴承", "SPINDLE-BEARING"),
    ("主轴", "SPINDLE-BEARING"),
    ("泄漏", "COOLANT-LEAK"),
    ("漏液", "COOLANT-LEAK"),
    ("液压", "HYDRAULIC-FAULT"),
    ("加热圈", "HEATER-AGING"),
    ("加热", "HEATER-AGING"),
    ("温控", "TEMP-DRIFT"),
    ("异响", "ABNORMAL-NOISE"),
    ("振动", "VIB-OVER"),
    ("温度", "TEMP-OVER"),
    ("过热", "TEMP-OVER"),
    ("压力", "PRESS-OVER"),
    ("转速", "RPM-OVER"),
)

# 自动沉淀开关：环境变量置 "0" 即关闭（默认开）
AUTO_KNOWLEDGE_ENV = "MES_AUTO_KNOWLEDGE"

# 工单描述里引出「推断根因」的常见词
_ROOT_CAUSE_MARKERS = ("疑似", "可能", "判断为", "原因为", "原因是")


def infer_fault_code(*texts: str | None) -> str:
    """按症状文本推断故障码；都不沾边时归 OTHER（仍可被关键词检索命中）"""
    blob = " ".join(str(t) for t in texts if t)
    for keyword, code in FAULT_CODE_HINTS:
        if keyword in blob:
            return code
    return "OTHER"


def _extract_root_cause(fault_desc: str) -> str:
    """从工单描述里取出「疑似…」这类推断句；没有就返回空串交给人工补。

    工单描述是巡检自动生成的，根因往往以「疑似主轴轴承磨损复发」这样的
    从句出现，直接把它切出来比把整段描述同时塞进 symptom 和 root_cause 有用。
    """
    text = (fault_desc or "").strip()
    for marker in _ROOT_CAUSE_MARKERS:
        idx = text.find(marker)
        if idx > 0:
            return text[idx:].strip(" ，,。;；")
    return ""


def knowledge_for_work_order(wo_id: str) -> dict | None:
    """取工单沉淀出的知识条目（看板/卡片展示「已沉淀」用）"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge WHERE source_wo_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (wo_id,),
        ).fetchone()
    return dict(row) if row else None


def sediment_knowledge_from_work_order(wo_id: str) -> dict | None:
    """把已闭环工单沉淀成一条知识；已沉淀过 / 未闭环 / 开关关闭时返回 None。

    只认 closed：未闭环的工单没有处置结果，沉淀进去是废料，还会污染后续检索。
    幂等键是 source_wo_id —— 同一张工单无论被关几次，都只产出一条。
    """
    if os.environ.get(AUTO_KNOWLEDGE_ENV, "1") == "0":
        return None
    wo = get_work_order(wo_id)
    if wo is None or wo["status"] != "closed":
        return None
    if knowledge_for_work_order(wo_id):
        return None

    equipment_name = ""
    rows = query_equipment(wo["equipment_id"])
    if rows:
        equipment_name = rows[0].get("name") or ""

    fault_desc = (wo.get("fault_desc") or "").strip()
    # 处置结果优先取工单 plan（关单时回填），其次时间线里最后一条 resolution
    resolution = (wo.get("plan") or "").strip()
    if not resolution:
        with get_db() as conn:
            row = conn.execute(
                "SELECT detail FROM work_order_log WHERE wo_id = ? AND action = 'resolution' "
                "ORDER BY ts DESC, rowid DESC LIMIT 1",
                (wo_id,),
            ).fetchone()
        resolution = (row["detail"] if row else "").strip()
    if not resolution:
        resolution = f"现场已完成处置，细节见工单 {wo_id}"

    root_cause = _extract_root_cause(fault_desc) or "工单未记录根因，建议人工补充"
    fault_code = infer_fault_code(fault_desc, wo.get("plan"), equipment_name)
    # 症状里带上设备编号与名称：既方便人看，也让「按设备查同类故障」能命中
    symptom = f"{wo['equipment_id']} {equipment_name}：{fault_desc}" if equipment_name else \
        f"{wo['equipment_id']}：{fault_desc}"

    saved = save_knowledge(
        fault_code=fault_code,
        symptom=symptom,
        root_cause=root_cause,
        resolution=resolution,
        source_wo_id=wo_id,
    )
    # 挂到工单时间线上：点开工单就能看到「这次闭环产出了哪条经验」
    with get_db() as conn:
        conn.execute(
            "INSERT INTO work_order_log(wo_id, action, detail, operator, ts) "
            "VALUES (?,?,?,?,?)",
            (wo_id, "knowledge_sediment", f"{saved['id']} · {fault_code}", "系统", now()),
        )
    return {
        "id": saved["id"],
        "fault_code": fault_code,
        "symptom": symptom,
        "source_wo_id": wo_id,
    }


def _try_sediment_quietly(wo_id: str) -> None:
    """沉淀入口的唯一职责边界：它绝不能让关单失败"""
    try:
        sediment_knowledge_from_work_order(wo_id)
    except Exception as exc:  # noqa: BLE001 —— 关单是主业务，沉淀是附加动作
        print(f"[knowledge] 工单 {wo_id} 自动沉淀失败：{exc}", flush=True)


# --------------------------------------------------------------------------- #
# 采集入库（网关 / 模拟器 → metric 表）
# --------------------------------------------------------------------------- #
def normalize_ts(ts: str | None) -> str:
    """把上报时间戳统一到秒级 'YYYY-MM-DD HH:MM:SS'

    网关侧常见格式不统一：ISO8601（带 T / Z / 毫秒 / 时区）与本地时间混用。
    不统一就会导致 ORDER BY ts 排序错乱，进而让巡检读到"旧的最新值"。
    """
    if not ts:
        return now()
    text = str(ts).strip()
    text = text.replace("T", " ").replace("Z", "")
    text = text.split("+")[0].split(".")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise MetricError(f"时间格式无法解析: {ts}（应为 YYYY-MM-DD HH:MM:SS 或 ISO8601）")


def _coerce_metric_values(**kwargs) -> dict[str, float]:
    """逐字段校验类型与物理区间，返回清洗后的数值字典"""
    cleaned: dict[str, float] = {}
    for field in METRIC_FIELDS:
        raw = kwargs.get(field)
        if raw is None or raw == "":
            continue
        if isinstance(raw, bool):
            raise MetricError(f"{field} 必须是数字，收到布尔值")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise MetricError(f"{field} 必须是数字，收到 {raw!r}") from None
        low, high = METRIC_RANGES[field]
        if not (low <= value <= high):
            raise MetricError(f"{field}={value} 超出物理合理区间 [{low}, {high}]")
        cleaned[field] = value
    if not cleaned:
        raise MetricError(
            f"未提供任何有效指标（可用字段: {', '.join(METRIC_FIELDS)}）"
        )
    return cleaned


def insert_metric(
    equipment_id: str | None,
    ts: str | None = None,
    temp: float | None = None,
    vibration: float | None = None,
    pressure: float | None = None,
    rpm: float | None = None,
    run_hours: float | None = None,
) -> dict:
    """写入一条设备运行指标（采集入口，幂等）

    约束：
      - equipment_id 必须已在设备台账登记（否则网关编码映射没对齐，入库也无法关联工单）
      - 数值必须落在 METRIC_RANGES 物理区间内，超区间直接拒绝
      - 同一设备同一秒重复上报视为同一条（INSERT OR IGNORE + 确定性 id），
        返回 duplicate=True，避免网关重试把点数翻倍
    """
    eq = (equipment_id or "").strip()
    if not eq:
        raise MetricError("缺少 equipment_id")
    if not query_equipment(eq):
        raise EquipmentNotFound(f"设备不存在: {eq}（请先在设备台账登记该编码）")

    ts_text = normalize_ts(ts)
    values = _coerce_metric_values(
        temp=temp, vibration=vibration, pressure=pressure, rpm=rpm, run_hours=run_hours
    )
    # 确定性主键：同设备同秒 → 同一条，天然幂等（不依赖自增/随机 id）
    mid = f"M-{eq}-{ts_text.replace('-', '').replace(':', '').replace(' ', '')}"

    with get_db() as conn:
        existed = conn.execute("SELECT 1 FROM metric WHERE id = ?", (mid,)).fetchone()
        if existed:
            return {"id": mid, "equipment_id": eq, "ts": ts_text, "duplicate": True}
        conn.execute(
            "INSERT INTO metric(id, equipment_id, ts, temp, vibration, pressure, rpm, run_hours) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                mid, eq, ts_text,
                values.get("temp"), values.get("vibration"), values.get("pressure"),
                values.get("rpm"), values.get("run_hours"),
            ),
        )
    return {
        "id": mid,
        "equipment_id": eq,
        "ts": ts_text,
        "values": values,
        "duplicate": False,
    }


def latest_metric_row(equipment_id: str) -> dict | None:
    """取设备最新一条指标（供模拟器续算 run_hours 等状态）"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM metric WHERE equipment_id = ? ORDER BY ts DESC, rowid DESC LIMIT 1",
            (equipment_id,),
        ).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------- #
# 巡检 / 异常判定
# --------------------------------------------------------------------------- #
def check_latest_metric(equipment_id: str) -> dict | None:
    """读取设备最新一条运行指标，返回是否超阈值及超限项"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM metric WHERE equipment_id = ? ORDER BY ts DESC, rowid DESC LIMIT 1",
            (equipment_id,),
        ).fetchone()
    if not row:
        return None
    m = dict(row)
    over = {k: v for k, v in THRESHOLDS.items() if m.get(k, 0) and m[k] > v}
    return {"metric": m, "over_threshold": over}
