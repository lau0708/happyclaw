"""demo.py — 端到端闭环演示（不依赖 mcp 包，仅标准库）

模拟三个 Agent（数据Agent / 诊断Agent / 工单Agent）的调用链，
跑通「报警 → 采集 → 诊断 → 建单 → 处置 → 复盘 → 经验回流」完整闭环。

运行：先 python init_db.py，再 python demo.py
"""
from __future__ import annotations

import sys

import db

# --------------------------------------------------------------------------- #
# 模拟产线报警
# --------------------------------------------------------------------------- #
ALARM_EQUIP = "CNC-03"
ALARM_MSG = "设备 CNC-03 最新振动值超阈值（>8.0 mm/s），触发产线报警"


def step1_alarm() -> None:
    print("=" * 66)
    print("① 事件触发：产线报警 / 定时巡检")
    print("   ", ALARM_MSG)
    latest = db.check_latest_metric(ALARM_EQUIP)
    print(f"    最新指标: {latest['metric']['ts']}  振动={latest['metric']['vibration']}mm/s  "
          f"超限项={list(latest['over_threshold'].keys())}")


# --------------------------------------------------------------------------- #
# 数据Agent：拉设备上下文
# --------------------------------------------------------------------------- #
def step2_data_agent() -> dict:
    print("\n② 数据Agent（工具: query_equipment / query_history_faults / search_knowledge）")
    equip = db.query_equipment(ALARM_EQUIP)[0]
    print(f"    设备台账: {equip['name']} | 类型={equip['type']} | 线别={equip['line']} | 状态={equip['status']}")
    faults = db.query_history_faults(ALARM_EQUIP)
    print(f"    历史故障({len(faults)}条):")
    for f in faults:
        print(f"      · {f['fault_code']}: {f['fault_desc']} → 根因[{f['root_cause']}] 处置[{f['resolution']}]")
    context = {
        "equipment_id": ALARM_EQUIP,
        "name": equip["name"],
        "type": equip["type"],
        "line": equip["line"],
        "history_faults": faults,
    }
    return context


# --------------------------------------------------------------------------- #
# 诊断Agent：命中知识 → 生成排查方案
# --------------------------------------------------------------------------- #
def step3_diagnose(context: dict, first_time: bool) -> dict:
    print("\n③ 诊断Agent（工具: search_knowledge / save_knowledge）")
    hits = db.search_knowledge("振动")
    plan: dict
    if hits:
        k = hits[0]
        plan = {
            "hit": True,
            "fault_code": k["fault_code"],
            "root_cause": k["root_cause"],
            "resolution": k["resolution"],
        }
        if not first_time:
            print(f"    【经验回流命中】症状『振动』命中知识 {k['id']}，置信度高")
        print(f"    根因假设: {k['root_cause']}")
        print(f"    处置方案: {k['resolution']}")
    else:
        plan = {
            "hit": False,
            "fault_code": "UNKNOWN",
            "root_cause": "待现场排查",
            "resolution": "停机检查主轴异响→测轴承游隙→定位故障",
        }
        print("    未命中知识，进入现场排查流程")
    return plan


# --------------------------------------------------------------------------- #
# 工单Agent：建单 + 推送
# --------------------------------------------------------------------------- #
def step4_work_order(plan: dict) -> str:
    print("\n④ 工单Agent（工具: create_work_order → 飞书推送）")
    wo = db.create_work_order(
        ALARM_EQUIP, f"{plan['root_cause']}；处置方案：{plan['resolution']}",
        severity="high",
    )
    print(f"    ✔ 工单 {wo['wo_id']} 已创建（severity=high），已推送维修班")
    print(f"    ┌─ 工单内容 ──────────────────────────────────────────")
    print(f"    │ 设备: {ALARM_EQUIP}   状态: {wo['status']}")
    print(f"    │ 描述: 振动超限，疑似{plan['root_cause']}")
    print(f"    │ 方案: {plan['resolution']}")
    print(f"    └────────────────────────────────────────────────────")
    return wo["wo_id"]


# --------------------------------------------------------------------------- #
# 人工处置反馈
# --------------------------------------------------------------------------- #
def step5_resolution(wo_id: str) -> str:
    print("\n⑤ 人工处置反馈（工具: update_work_order）")
    # 模拟维修工完成后回填
    resolution = "按方案更换主轴轴承并校准动平衡，复测振动降至 5.2mm/s，恢复正常"
    db.update_work_order(wo_id, status="done", resolution=resolution, operator="维修工-张工")
    wo = db.get_work_order(wo_id)
    print(f"    ✔ 工单 {wo_id} 已闭环，status={wo['status']}")
    print(f"    回填: {wo['plan']}")
    return resolution


# --------------------------------------------------------------------------- #
# 复盘沉淀
# --------------------------------------------------------------------------- #
def step6_review(wo_id: str, resolution: str) -> None:
    print("\n⑥ 复盘沉淀（工具: save_knowledge → 写入知识库 / Workspace Memory）")
    db.save_knowledge(
        fault_code="SPINDLE-BEARING",
        symptom="主轴振动超限 主轴异响",
        root_cause="主轴轴承磨损",
        resolution=resolution,
        source_wo_id=wo_id,
    )
    print(f"    ✔ 已沉淀经验: 主轴振动超限 → 主轴轴承磨损 → {resolution[:20]}…")


def main() -> None:
    print(f"\n██████ 场景一：首次报警（未命中历史经验）██████")
    step1_alarm()
    ctx = step2_data_agent()
    plan1 = step3_diagnose(ctx, first_time=True)
    wo1 = step4_work_order(plan1)
    res1 = step5_resolution(wo1)
    step6_review(wo1, res1)

    print(f"\n██████ 场景二：两周后同类报警（经验回流命中）██████")
    step1_alarm()
    step2_data_agent()
    plan2 = step3_diagnose(ctx, first_time=False)  # 此时知识库已含刚才沉淀的经验
    wo2 = step4_work_order(plan2)
    step5_resolution(wo2)

    print("\n" + "=" * 66)
    print("✔ 闭环完成：报警→采集→诊断→建单→处置→复盘→经验回流，全部打通。")
    print("  后续同类报警将由诊断Agent直接命中历史处置方案（越用越准）。")


if __name__ == "__main__":
    sys.exit(main())
