"""workorder_flow.py — 经 MCP 协议跑通「建单 → 处置 → 复盘 → 经验回流」工单闭环

与 demo.py（直接调 db）不同，本脚本通过标准 MCP Client 连接 server.py，
以 Agent 调用工具的视角走完工单下半段闭环，验证：
  工单创建(open) → 处置回填(done) → 复盘沉淀(知识库) → 经验回流命中

运行（需先 python init_db.py，server.py 依赖已装）：
    .venv/bin/python workorder_flow.py
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


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            print(f"已注册 MCP 工具（{len(tools)}）：{tools}\n")

            # FastMCP 对 list 返回会拆成多条独立 JSON content。
            # list 型工具始终返回 list；单对象型工具返回对象本身。
            async def call_list(name: str, args: dict) -> list:
                res = await session.call_tool(name, args)
                return [json.loads(c.text) for c in res.content if hasattr(c, "text")]

            async def call_obj(name: str, args: dict) -> dict:
                res = await session.call_tool(name, args)
                return json.loads(next(c.text for c in res.content if hasattr(c, "text")))

            # ① 巡检确认 CNC-03 状态
            print("—— ① 数据/巡检：CNC-03 最新指标 ——")
            metric = await call_list("query_metric", {"equipment_id": "CNC-03", "hours": 24})
            latest = metric[-1]
            print(f"   振动 {latest['vibration']}mm/s（阈值 8.0） | "
                  f"温度 {latest['temp']}℃ | 压力 {latest['pressure']}")

            # ② 建单
            print("\n—— ② 工单Agent：create_work_order ——")
            wo = await call_obj("create_work_order", {
                "equipment_id": "CNC-03",
                "fault_desc": "主轴振动 12.7mm/s 超阈(8.0) 达 59%，疑似主轴轴承磨损复发",
                "severity": "high",
            })
            wo_id = wo["wo_id"]
            print(f"   ✔ 已建单 {wo_id}  状态={wo['status']}")

            # ③ 处置回填
            print("\n—— ③ 维修反馈：update_work_order ——")
            upd = await call_obj("update_work_order", {
                "wo_id": wo_id,
                "status": "done",
                "resolution": "更换主轴轴承并校准动平衡，复测振动降至 5.2mm/s，恢复正常",
                "operator": "维修工-张工",
            })
            print(f"   ✔ 工单已闭环  状态={upd['status']}  回填={upd.get('plan')}")

            # ④ 复盘沉淀
            print("\n—— ④ 复盘：save_knowledge ——")
            await call_obj("save_knowledge", {
                "fault_code": "SPINDLE-BEARING",
                "symptom": "主轴振动超限 主轴异响",
                "root_cause": "主轴轴承磨损",
                "resolution": "更换主轴轴承并校准动平衡，复测振动5.2mm/s",
                "source_wo_id": wo_id,
            })
            print("   ✔ 已沉淀经验 → 知识库 / Workspace Memory")

            # ⑤ 验证：工单列表 + 经验回流命中
            print("\n—— ⑤ 验证：工单状态 + 经验回流 ——")
            wos = await call_list("query_work_orders", {"equipment_id": "CNC-03"})
            print(f"   CNC-03 工单数：{len(wos)}；最新工单 {wos[-1]['wo_id']} 状态={wos[-1]['status']}")
            hits = await call_list("search_knowledge", {"symptom_keyword": "振动"})
            print(f"   知识库命中：{hits[0]['id'] if hits else '无'} → "
                  f"{hits[0]['root_cause'] if hits else ''}")

    print("\n✔ 工单闭环完成：报警→诊断→建单→处置→复盘→经验回流 全部经 MCP 打通。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
