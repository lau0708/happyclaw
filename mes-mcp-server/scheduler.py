"""scheduler.py — 模拟 HappyClaw Cron 定时巡检 Agent（仅标准库）

周期性对设备做阈值巡检，检测到异常自动触发「诊断 → 建单 → 推送」，
并对同一设备的未闭环工单做幂等去重（避免重复建单）。

在真实 HappyClaw 中，等价实现为：一个 Cron 定时任务 + 巡检 Agent，
对每个设备调用 check_latest_metric，超限则转入诊断/工单 Agent。

用法：
    python3 scheduler.py --once          # 单次巡检（演示用）
    python3 scheduler.py --interval 30   # 每 30 秒巡检一次（持续运行）
    python3 scheduler.py --devices CNC-03,INJ-01   # 指定设备
"""
from __future__ import annotations

import argparse
import sys
import time

import agent_nodes
import db
import feishu


def has_open_work_order(equipment_id: str) -> bool:
    """幂等：该设备若已有未闭环工单，则本次跳过，避免重复建单"""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT wo_id FROM work_order WHERE equipment_id = ? "
            "AND status NOT IN ('done','closed') LIMIT 1",
            (equipment_id,),
        ).fetchone()
    return row is not None


def inspect_device(equipment_id: str) -> None:
    """巡检单个设备：取最新指标，超阈值则诊断并建单"""
    res = db.check_latest_metric(equipment_id)
    if not res:
        print(f"[{db.now()}] {equipment_id}: 无最新指标，跳过")
        return
    over = res["over_threshold"]
    m = res["metric"]
    if not over:
        print(f"[{db.now()}] {equipment_id}: 正常 振动={m['vibration']} 温度={m['temp']} "
              f"压力={m['pressure']} ✔")
        return

    # 超阈值 → Agent 处理
    print(f"[{db.now()}] ⚠ {equipment_id}: 超限项={list(over.keys())} "
          f"振动={m['vibration']} 温度={m['temp']}")
    if has_open_work_order(equipment_id):
        print(f"    ↳ 已有未闭环工单，幂等跳过（避免重复建单）")
        return

    # 诊断节点：LLM 可用时走模型（事实包喂入，数字可对账），
    # 未配置 LLM_API_KEY 自动降级为知识库命中
    plan = agent_nodes.diagnose(equipment_id)
    root = plan["root_cause"]
    resolution = plan["resolution"]

    # 工单Agent：建单 + 推送
    wo = db.create_work_order(equipment_id, f"巡检发现{list(over.keys())}超限，疑似{root}",
                              severity="high")
    print(f"    ↳ 诊断: {root}（置信度 {plan['confidence']}，"
          f"{'LLM' if agent_nodes.llm_enabled() else '知识库降级'}）")
    print(f"    ↳ 处置: {resolution}")
    # 飞书推送：卡片消息（未配置 webhook 时跳过，不阻断）
    equip = db.query_equipment(equipment_id)
    equip_name = f"{equipment_id} {equip[0]['name']}" if equip else equipment_id
    ok, note = feishu.send_work_order_card(
        wo_id=wo["wo_id"], equipment=equip_name, root_cause=root,
        resolution=resolution, severity="high",
        confidence=plan["confidence"], over_items=list(over.keys()),
    )
    if ok:
        print(f"    ↳ 工单 {wo['wo_id']} 已创建（severity=high），飞书卡片已推送 📋")
    else:
        print(f"    ↳ 工单 {wo['wo_id']} 已创建（severity=high），飞书未推送（{note}）")


def run_once(devices: list[str]) -> None:
    print(f"===== 定时巡检 · {db.now()} · 设备 {devices} =====")
    for eq in devices:
        inspect_device(eq)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="单次巡检后退出")
    parser.add_argument("--interval", type=int, default=30, help="巡检间隔（秒）")
    parser.add_argument("--devices", default=None, help="设备列表，逗号分隔，默认全部")
    args = parser.parse_args()

    if args.devices:
        devices = [d.strip() for d in args.devices.split(",") if d.strip()]
    else:
        devices = [e["equipment_id"] for e in db.query_equipment()]

    if args.once:
        run_once(devices)
        return

    print(f"持续巡检模式：每 {args.interval}s 巡检 {devices}（Ctrl+C 退出）")
    while True:
        try:
            run_once(devices)
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n已停止巡检。")
            break


if __name__ == "__main__":
    sys.exit(main())
