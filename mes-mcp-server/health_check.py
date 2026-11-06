"""health_check.py — 主动查询触发：人让 Agent 查设备是否故障

模拟操作工/维修工经对话主动让「设备运维专家」检查设备健康状态：
  Agent 通过 MCP 调 query_metric + check_latest_metric 查真实数据 → 健康判定
  异常设备 → 建议进入 诊断→建单 流程；健康设备 → 判定正常。

区别于自动报警（系统被动触发），这是闭环的**主动触发**起点。

运行：
    .venv/bin/python health_check.py [设备ID]...
默认检查 CNC-03（异常）与 CNC-01（健康）作对比。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SERVER = str(Path(__file__).resolve().parent / "server.py")
DEFAULT_DEVS = ["CNC-03", "CNC-01"]


async def main() -> int:
    devices = sys.argv[1:] or DEFAULT_DEVS
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call_list(name: str, args: dict) -> list:
                res = await session.call_tool(name, args)
                return [json.loads(c.text) for c in res.content if hasattr(c, "text")]

            async def call_obj(name: str, args: dict) -> dict | None:
                res = await session.call_tool(name, args)
                return json.loads(next(c.text for c in res.content if hasattr(c, "text")))

            for equip in devices:
                print(f"\n—— 主动查询：请检查设备 {equip} 是否故障 ——")
                metric = await call_list("query_metric", {"equipment_id": equip, "hours": 24})
                if not metric:
                    print(f"   未查到 {equip} 的运行指标（设备号有误？）")
                    continue
                latest = metric[-1]
                chk = await call_obj("check_latest_metric", {"equipment_id": equip})
                over = (chk or {}).get("over_threshold", {}) or {}
                print(f"   最新指标: 振动 {latest['vibration']}mm/s | "
                      f"温度 {latest['temp']}℃ | 压力 {latest['pressure']} | 转速 {latest['rpm']}")
                if over:
                    # 超限项：显示「实际值 > 阈值」（处理温度键名 temperature↔temp 映射）
                    metric_key = {"temperature": "temp"}.get
                    items = ", ".join(
                        f"{k} {latest.get(metric_key(k) or k)} > 阈值 {v}" for k, v in over.items()
                    )
                    print(f"   ❌ 判定：异常（{items}）")
                    print(f"   → 建议：进入 诊断→建单→处置 闭环流程")
                else:
                    print("   ✅ 判定：健康，各项指标在阈值内，无需处置")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
