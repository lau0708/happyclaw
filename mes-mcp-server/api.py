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


def handle(path: str, query: dict) -> tuple[int, object]:
    """纯函数路由：返回 (状态码, JSON可序列化数据)。可独立测试。"""
    if path == "/api/health":
        return 200, {"ok": True, "db": str(db.DB_PATH)}
    if path == "/api/equipment":
        return 200, db.query_equipment(query.get("equipment_id"))
    if path == "/api/work_orders":
        return 200, db.query_work_orders(query.get("status"), query.get("equipment_id"))
    if path.startswith("/api/work_orders/") and path.endswith("/close"):
        wo_id = path.split("/")[3]
        return 200, db.close_work_order(wo_id, "人工回填闭环", operator="看板")
    if path.startswith("/api/work_orders/"):
        wo_id = path.rsplit("/", 1)[-1]
        return 200, db.get_work_order(wo_id)
    if path.startswith("/api/metric/"):
        eq = path.rsplit("/", 1)[-1]
        return 200, db.query_metric(eq, int(query.get("hours", "24")))
    if path.startswith("/api/history_faults/"):
        eq = path.rsplit("/", 1)[-1]
        return 200, db.query_history_faults(eq)
    if path == "/api/knowledge":
        return 200, db.list_knowledge()
    if path == "/api/latest_status":
        return 200, db.latest_equipment_status()
    return 404, {"error": "not found"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 精简日志
        pass

    def _respond(self):
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            code, data = handle(parsed.path, query)
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
