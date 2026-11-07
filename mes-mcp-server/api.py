"""api.py — 轻量 HTTP JSON API（仅标准库 http.server），供看板取数 + 网关上报

端点分两类：
  只读查询：equipment / metric / work_orders / knowledge / latest_status
  写入接口：
    POST /api/metric            采集网关上报单条指标（需 X-API-Key）
    POST /api/metric/batch      网关批量补传（需 X-API-Key）
    POST /api/work_orders/...   工单派单 / 回填 / 闭环
    POST /api/knowledge/create  经验沉淀

鉴权：设置了环境变量 INGEST_API_KEY 时，上报必须带同值 X-API-Key 头；
未设置时只接受本机（127.0.0.1）上报，便于本地联调，跨机上报请务必配置密钥。

用法：python3 api.py [--port 8002]
看板通过 vite proxy 将 /mes-api 转发到本服务。
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import db

db.load_env()

PORT = 8002
INGEST_KEY_HEADER = "X-API-Key"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    """读取请求体 JSON（空体返回空 dict）"""
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _with_knowledge(wo: dict | None) -> dict | None:
    """给工单补上「已沉淀的知识条目」，卡片与看板据此显示 KN 号。

    只做只读拼装，不在工单表冗余字段：归属关系只在 knowledge.source_wo_id。
    """
    if not wo:
        return wo
    kn = db.knowledge_for_work_order(wo["wo_id"])
    if kn:
        wo["knowledge_id"] = kn["id"]
        wo["knowledge_fault_code"] = kn["fault_code"]
    return wo


def _close_work_order(
    wo_id: str, operator: str | None = None, resolution: str | None = None
) -> tuple[int, object]:
    """看板关单：经状态机合法推进到 closed，错误返回 404/409 而非静默 200

    operator 由调用方给：飞书卡片按钮点击时是真实点击人，看板兜底用「看板」。
    resolution 只在调用方真的回填了处置结果时才传 —— 传占位文案会覆盖掉
    done 环节已经写好的处置方案，知识库沉淀出来的经验就失真了。
    关单成功会自动沉淀一条知识（db.close_work_order 内触发），响应带回 KN 号。
    """
    if db.get_work_order(wo_id) is None:
        return 404, {"error": f"工单不存在: {wo_id}"}
    try:
        wo = db.close_work_order(
            wo_id, resolution or "", operator=operator or "看板"
        )
        return 200, _with_knowledge(wo)
    except db.WorkOrderError as e:
        return 409, {"error": str(e)}


def _wo_transition(wo_id: str, body: dict, target: str) -> tuple[int, object]:
    """工单状态推进：assign=派单（需 operator），done=处置回填（需 resolution）"""
    wo = db.get_work_order(wo_id)
    if wo is None:
        return 404, {"error": f"工单不存在: {wo_id}"}
    operator = (body or {}).get("operator")
    resolution = (body or {}).get("resolution")
    if target == "assigned" and not operator:
        return 400, {"error": "派单必须提供 operator（维修人）"}
    if target == "done" and not resolution:
        return 400, {"error": "处置回填必须提供 resolution（处置结果）"}
    try:
        return 200, _with_knowledge(
            db.update_work_order(
                wo_id, status=target, resolution=resolution, operator=operator
            )
        )
    except db.WorkOrderError as e:
        return 409, {"error": str(e)}


def _wo_logs(wo_id: str) -> tuple[int, object]:
    """工单全生命周期留痕"""
    if db.get_work_order(wo_id) is None:
        return 404, {"error": f"工单不存在: {wo_id}"}
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT action, detail, operator, ts FROM work_order_log "
            "WHERE wo_id = ? ORDER BY ts, rowid",
            (wo_id,),
        ).fetchall()
    return 200, [dict(r) for r in rows]


def _inspect(equipment_id: str) -> tuple[int, object]:
    """手动触发单设备巡检：复用 scheduler.inspect_device，返回判异与建单结果"""
    if not db.query_equipment(equipment_id):
        return 404, {"error": f"设备不存在: {equipment_id}"}
    import scheduler

    before = len(db.query_work_orders(equipment_id=equipment_id))
    scheduler.inspect_device(equipment_id)
    after = len(db.query_work_orders(equipment_id=equipment_id))
    check = db.check_latest_metric(equipment_id)
    return 200, {
        "equipment_id": equipment_id,
        "over_threshold": list((check or {}).get("over_threshold", {}).keys()),
        "new_work_order": after > before,
        "open_work_orders": after,
    }


def _ingest_auth(headers: dict | None, client_ip: str) -> tuple[bool, int, str]:
    """上报鉴权：已配密钥则严格比对；未配密钥只放行本机

    返回 (是否放行, 失败状态码, 失败原因)。用 hmac.compare_digest 避免时序侧信道。
    """
    expected = (os.environ.get("INGEST_API_KEY") or "").strip()
    provided = ""
    for key, value in (headers or {}).items():
        if str(key).lower() == INGEST_KEY_HEADER.lower():
            provided = (value or "").strip()
            break
    if expected:
        if provided and hmac.compare_digest(provided, expected):
            return True, 200, ""
        return False, 401, f"{INGEST_KEY_HEADER} 缺失或不匹配"
    if client_ip in LOOPBACK:
        return True, 200, ""
    return False, 403, (
        "服务端未配置 INGEST_API_KEY，仅接受本机上报；"
        "网关跨机上报请在 .env 设置 INGEST_API_KEY"
    )


def _ingest_one(item: dict) -> dict:
    """单条指标入库（校验失败抛 db.MetricError 子类）"""
    return db.insert_metric(
        item.get("equipment_id"),
        ts=item.get("ts"),
        temp=item.get("temp"),
        vibration=item.get("vibration"),
        pressure=item.get("pressure"),
        rpm=item.get("rpm"),
        run_hours=item.get("run_hours"),
    )


def _ingest_single(body: dict | None, headers: dict | None, client_ip: str) -> tuple[int, object]:
    ok, code, reason = _ingest_auth(headers, client_ip)
    if not ok:
        return code, {"error": reason}
    try:
        return 200, _ingest_one(body or {})
    except db.EquipmentNotFound as e:
        return 404, {"error": str(e)}
    except db.MetricError as e:
        return 400, {"error": str(e)}


def _ingest_batch(body: dict | None, headers: dict | None, client_ip: str) -> tuple[int, object]:
    """批量补传：逐条校验，坏点不影响好点（断网恢复后的续传场景）"""
    ok, code, reason = _ingest_auth(headers, client_ip)
    if not ok:
        return code, {"error": reason}
    items = (body or {}).get("metrics")
    if not isinstance(items, list) or not items:
        return 400, {"error": '请求体需为 {"metrics": [ {...}, ... ]}'}

    accepted = duplicates = 0
    errors: list[dict] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append({"index": index, "error": "条目必须是对象"})
            continue
        try:
            row = _ingest_one(item)
        except db.MetricError as e:
            errors.append({"index": index, "error": str(e)})
            continue
        if row.get("duplicate"):
            duplicates += 1
        else:
            accepted += 1
    return 200, {
        "received": len(items),
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": len(errors),
        "errors": errors[:20],
    }


def find_free_port(preferred: int = PORT, tries: int = 30) -> int:
    """指定端口被占用时，自动顺延找下一个空闲端口（规避 Docker 等占用冲突）"""
    for p in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    raise OSError(f"端口 {preferred}~{preferred + tries - 1} 均被占用")


def handle(
    path: str,
    query: dict,
    body: dict | None = None,
    headers: dict | None = None,
    client_ip: str = "127.0.0.1",
) -> tuple[int, object]:
    """纯函数路由：返回 (状态码, JSON可序列化数据)。可独立测试。

    GET：只读查询；POST：指标上报 / 状态推进 / 知识沉淀 / 手动巡检。
    """
    if path == "/api/health":
        return 200, {
            "ok": True,
            "db": str(db.DB_PATH),
            "ingest_auth": "api-key" if os.environ.get("INGEST_API_KEY") else "loopback-only",
        }
    if path == "/api/metric" and body is not None:
        return _ingest_single(body, headers, client_ip)
    if path == "/api/metric/batch":
        return _ingest_batch(body, headers, client_ip)
    if path == "/api/metric":
        return 405, {
            "error": "用法：POST /api/metric 上报单条指标；"
                     "查询请用 GET /api/metric/{equipment_id}?hours=24"
        }
    if path == "/api/equipment":
        return 200, db.query_equipment(query.get("equipment_id"))
    if path == "/api/work_orders":
        return 200, db.query_work_orders(query.get("status"), query.get("equipment_id"))
    if path.startswith("/api/work_orders/"):
        parts = path.split("/")  # /api/work_orders/{id}[/assign|/done|/close|/logs]
        wo_id = parts[3]
        if len(parts) == 5 and parts[4] == "assign":
            return _wo_transition(wo_id, body, "assigned")
        if len(parts) == 5 and parts[4] == "done":
            return _wo_transition(wo_id, body, "done")
        if len(parts) == 5 and parts[4] == "close":
            return _close_work_order(
                wo_id,
                (body or {}).get("operator"),
                (body or {}).get("resolution"),
            )
        if len(parts) == 5 and parts[4] == "logs":
            return _wo_logs(wo_id)
        if len(parts) == 4:
            wo = db.get_work_order(wo_id)
            return (200, _with_knowledge(wo)) if wo else (404, {"error": f"工单不存在: {wo_id}"})
    if path.startswith("/api/metric/"):
        eq = path.rsplit("/", 1)[-1]
        return 200, db.query_metric(eq, int(query.get("hours", "24")))
    if path.startswith("/api/history_faults/"):
        eq = path.rsplit("/", 1)[-1]
        return 200, db.query_history_faults(eq)
    if path == "/api/knowledge":
        q = query.get("q")
        if q:
            return 200, db.search_knowledge(q, limit=int(query.get("limit", "10")))
        return 200, db.list_knowledge()
    if path == "/api/knowledge/create":
        b = body or {}
        missing = [k for k in ("fault_code", "symptom", "root_cause", "resolution") if not b.get(k)]
        if missing:
            return 400, {"error": f"缺少必填字段: {', '.join(missing)}"}
        return 200, db.save_knowledge(
            b["fault_code"], b["symptom"], b["root_cause"], b["resolution"],
            source_wo_id=b.get("source_wo_id"),
        )
    if path.startswith("/api/inspect/"):
        return _inspect(path.rsplit("/", 1)[-1])
    if path == "/api/latest_status":
        return 200, db.latest_equipment_status()
    return 404, {"error": "not found"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 精简日志
        pass

    def _respond(self):
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        body = _read_json(self) if self.command == "POST" else None
        try:
            code, data = handle(
                parsed.path,
                query,
                body,
                headers=dict(self.headers),
                client_ip=self.client_address[0],
            )
        except Exception as e:  # noqa: BLE001
            code, data = 500, {"error": str(e)}
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.end_headers()

    do_GET = do_POST = _respond


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--strict-port",
        action="store_true",
        help="端口被占用时直接报错退出，不自动顺延（守护/探活场景必须用这个）",
    )
    args = parser.parse_args()
    # 默认顺延可规避 Docker 等占用冲突；但守护进程按固定端口探活，
    # 静默漂到 8003 会让探针永远失败、前端 vite proxy 也一起失联。
    port = args.port if args.strict_port else find_free_port(args.port)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    actual = server.server_address[1]
    print(f"✔ HTTP API 已启动: http://localhost:{actual}/api/health", flush=True)
    print(
        f"  前端 vite proxy 请指向该端口：MES_API_PORT={actual} npm run dev",
        flush=True,
    )
    auth_mode = "X-API-Key" if os.environ.get("INGEST_API_KEY") else "仅本机(未配置 INGEST_API_KEY)"
    print(f"  采集上报: POST /api/metric（鉴权: {auth_mode}）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
