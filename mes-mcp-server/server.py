"""server.py — 自研 mcp-server-mes：把模拟 MES 封装为 Agent 可调用的 MCP 工具

接入 HappyClaw：在智能体能力治理中注册本 MCP Server（stdio 或 SSE transport）。
依赖：pip install mcp
运行（stdio，供 HappyClaw 默认接入）：
    python server.py
运行（SSE，供 http 接入）：
    python server.py --transport sse
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 无论 HappyClaw 从哪个工作目录启动 MCP，都从本文件所在目录导入 db
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    sys.stderr.write("缺少依赖，请先安装：pip install mcp\n")
    raise

import db

from mcp.server.transport_security import TransportSecuritySettings

# 放行本机与 Docker 宿主机别名访问（否则 FastMCP 的 DNS rebinding 防护
# 会以 421 拒绝来自 host.docker.internal 的 SSE 连接）
mcp = FastMCP(
    "mes-server",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1:*", "localhost:*", "[::1]:*",
            "host.docker.internal:*",
        ],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    ),
)


# ----------------------------- 数据Agent 工具 ----------------------------- #
@mcp.tool()
def query_equipment(equipment_id: str | None = None) -> list[dict]:
    """查询设备台账与当前状态；不传设备ID返回全部设备"""
    return db.query_equipment(equipment_id)


@mcp.tool()
def query_metric(equipment_id: str, hours: int = 24) -> list[dict]:
    """查询设备最近 hours 小时的运行指标（温度/振动/压力/转速）"""
    return db.query_metric(equipment_id, hours)


@mcp.tool()
def query_history_faults(
    equipment_id: str | None = None, fault_code: str | None = None, limit: int = 5
) -> list[dict]:
    """查询设备历史故障与处置记录，供同类故障诊断参考"""
    return db.query_history_faults(equipment_id, fault_code, limit)


@mcp.tool()
def search_knowledge(symptom_keyword: str, limit: int = 5) -> list[dict]:
    """按症状关键词命中历史沉淀的故障处置经验"""
    return db.search_knowledge(symptom_keyword, limit)


# ----------------------------- 工单Agent 工具 ----------------------------- #
@mcp.tool()
def create_work_order(equipment_id: str, fault_desc: str, severity: str = "medium") -> dict:
    """创建设备异常工单，返回工单号。severity: low|medium|high|critical"""
    return db.create_work_order(equipment_id, fault_desc, severity)


@mcp.tool()
def update_work_order(
    wo_id: str, status: str, resolution: str | None = None, operator: str | None = None
) -> dict:
    """更新工单状态并回填处置结果。status: open|assigned|in_progress|done|closed"""
    return db.update_work_order(wo_id, status, resolution, operator)


@mcp.tool()
def query_work_orders(
    status: str | None = None, equipment_id: str | None = None
) -> list[dict]:
    """查询工单列表，可按状态/设备过滤（供看板与工单闭环追踪）"""
    return db.query_work_orders(status, equipment_id)


# ----------------------------- 诊断Agent 工具 ----------------------------- #
@mcp.tool()
def save_knowledge(
    fault_code: str, symptom: str, root_cause: str, resolution: str,
    source_wo_id: str | None = None,
) -> dict:
    """把故障处置经验沉淀到知识库（复盘环节），供后续同类故障命中"""
    return db.save_knowledge(fault_code, symptom, root_cause, resolution, source_wo_id)


# ----------------------------- 巡检辅助 ----------------------------- #
@mcp.tool()
def check_latest_metric(equipment_id: str) -> dict | None:
    """巡检：读取设备最新指标并判定是否超阈值，返回超限项"""
    return db.check_latest_metric(equipment_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport", choices=["stdio", "sse"], default="stdio",
        help="stdio 供 HappyClaw 容器内本地执行；sse 供容器经 host.docker.internal 访问宿主机",
    )
    parser.add_argument("--host", default="0.0.0.0", help="sse 模式绑定地址")
    parser.add_argument("--port", type=int, default=18080, help="sse 模式端口")
    args = parser.parse_args()

    if args.transport == "sse":
        # SSE：让 HappyClaw 沙箱容器通过 host.docker.internal:<port> 访问宿主机 MCP
        try:
            import uvicorn
        except ImportError:  # pragma: no cover
            sys.stderr.write("缺少依赖，请先安装：pip install uvicorn\n")
            raise
        app = mcp.sse_app()
        uvicorn.run(app, host=args.host, port=args.port)
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
