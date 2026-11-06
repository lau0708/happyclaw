"""api.py — 轻量 HTTP JSON API（仅标准库 http.server），供 Vue 工单看板取数

暴露只读查询端点 + 一个工单闭环端点，端口默认 8002。

用法：python3 api.py [--port 8002]
看板通过 vite proxy 将 /api 转发到本服务。
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import db


PORT = 8002


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    """读取请求体 JSON（空体返回空 dict）"""
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _close_work_order(wo_id: str) -> tuple[int, object]:
    """看板关单：经状态机合法推进到 closed，错误返回 404/409 而非静默 200"""
    if db.get_work_order(wo_id) is None:
        return 404, {"error": f"工单不存在: {wo_id}"}
    try:
        return 200, db.close_work_order(wo_id, "人工回填闭环", operator="看板")
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
        return 200, db.update_work_order(
            wo_id, status=target, resolution=resolution, operator=operator
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


def handle(path: str, query: dict, body: dict | None = None) -> tuple[int, object]:
    """纯函数路由：返回 (状态码, JSON可序列化数据)。可独立测试。

    GET：只读查询；POST：状态推进 / 知识沉淀 / 手动巡检。
    """
    if path == "/api/health":
        return 200, {"ok": True, "db": str(db.DB_PATH)}
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
            return _close_work_order(wo_id)
        if len(parts) == 5 and parts[4] == "logs":
            return _wo_logs(wo_id)
        if len(parts) == 4:
            wo = db.get_work_order(wo_id)
            return (200, wo) if wo else (404, {"error": f"工单不存在: {wo_id}"})
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
            code, data = handle(parsed.path, query, body)
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
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    do_GET = do_POST = _respond


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    # 若 --port 被占用（如 Docker），自动顺延到空闲端口
    port = find_free_port(args.port)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    actual = server.server_address[1]
    print(f"✔ HTTP API 已启动: http://localhost:{actual}/api/health")
    print(f"  前端 vite proxy 请指向该端口：MES_API_PORT={actual} npm run dev")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
