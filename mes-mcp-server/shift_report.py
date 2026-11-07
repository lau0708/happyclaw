"""shift_report.py — 班次巡检简报：每班收班时把当班情况汇总推群（仅标准库）

为什么要有它：飞书报警卡片是**事件驱动**的（一台设备越阈推一条），
班组长真正需要的是**一班一总**——当班哪些设备越过阈、哪些工单还挂着没人收尾。
卡片解决「立刻有人管」，简报解决「收班时知道有没有留坑」。

班次口径（两班倒，与车间排班一致）：
  早班 08:00–20:00
  晚班 20:00–次日 08:00
每到班次结束时刻（08:00 / 20:00）生成**刚结束那一班**的简报推送飞书群。

统计口径（三个刻意的选择）：
  1. 越阈设备按**当班窗口内的峰值**判定，不只看最后一刻 —— 中途越阈后被处置好的
     设备同样要曝光，否则夜班漏掉的问题会被早班抹平。
  2. `done`（已处理、待闭环）计入**未闭环** —— 它同样要人签字收尾，不算完。
  3. 闭环数按 work_order_log 的 `status_change → closed` 统计，不按 updated_at，
     因为工单表没有 closed_at，updated_at 还会被处置回填改写。

用法：
  python3 shift_report.py                        # 打印上一班简报（不发）
  python3 shift_report.py --send                 # 生成并推送
  python3 shift_report.py --at "2026-11-07 20:00" --send   # 指定班次边界（演示/补发）
  python3 shift_report.py --daemon               # 常驻：到点自动推送（supervise.mjs 托管）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta

import db
import feishu

DAY_START_HOUR = 8
NIGHT_START_HOUR = 20
SHIFT_HOURS = 12

# 指标中文名与单位（简报里给人看，不露字段名）
METRIC_META = {
    "temp": ("温度", "℃"),
    "vibration": ("振动", "mm/s"),
    "pressure": ("压力", "MPa"),
}


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_at(text: str | None) -> datetime | None:
    """解析 --at / API ?at=，接受 'YYYY-MM-DD HH:MM[:SS]' 与 ISO8601 的 T 分隔"""
    if not text:
        return None
    raw = str(text).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(f"时间格式无法解析: {text}（应为 YYYY-MM-DD HH:MM[:SS]）")


def shift_start(at: datetime) -> tuple[datetime, str]:
    """at 所在班次的起始时刻与班次名（早班 08:00 开班，晚班 20:00 开班）"""
    midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
    if DAY_START_HOUR <= at.hour < NIGHT_START_HOUR:
        return midnight.replace(hour=DAY_START_HOUR), "早班"
    if at.hour >= NIGHT_START_HOUR:
        return midnight.replace(hour=NIGHT_START_HOUR), "晚班"
    # 00:00–08:00 属于前一晚开班的晚班
    return (midnight - timedelta(days=1)).replace(hour=NIGHT_START_HOUR), "晚班"


def shift_window(
    at: datetime | None = None, ended: bool = False
) -> tuple[datetime, datetime, str, bool]:
    """返回 (start, end, 班次名, 是否为完整已结束班次)

    ended=True 且 at 恰在班次边界（08:00 / 20:00，分秒为 0）时，取**刚结束**的那一班；
    否则取 at 所在班次从开班到 at，即「当班至今」——手工查看/演示时更有用。
    """
    at = (at or datetime.now()).replace(microsecond=0)
    on_boundary = (
        at.minute == 0 and at.second == 0 and at.hour in (DAY_START_HOUR, NIGHT_START_HOUR)
    )
    if ended and on_boundary:
        name = "晚班" if at.hour == DAY_START_HOUR else "早班"
        return at - timedelta(hours=SHIFT_HOURS), at, name, True
    start, name = shift_start(at)
    return start, at, name, False


def _minutes_between(start_text: str | None, end: datetime) -> int:
    """粗算时长（分钟）；时间串解析不了就返回 0，不让一条脏数据毁掉整份简报"""
    if not start_text:
        return 0
    try:
        start = datetime.strptime(str(start_text)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return 0
    return max(0, int((end - start).total_seconds() // 60))


def _statuses_as_of(conn, end_s: str) -> dict[str, str]:
    """还原工单在 end 时刻的状态：由 work_order_log 最后一次流转回推，无日志即 open。

    不直接读 work_order.status，是为了让**补发历史班次**也说得准 ——
    否则昨天晚班的简报会因为今天有人关单，把当时挂着的工单抹掉。
    """
    statuses: dict[str, str] = {}
    for row in conn.execute(
        "SELECT wo_id, detail FROM work_order_log "
        "WHERE action = 'status_change' AND ts < ? ORDER BY ts, rowid",
        (end_s,),
    ).fetchall():
        target = (row["detail"] or "").split("→")[-1].strip()
        if target in db.WORK_ORDER_STATUSES:
            statuses[row["wo_id"]] = target
    return statuses


def _over_at(conn, equipment_id: str, end_s: str) -> list[str]:
    """设备在 end 时刻仍越阈的指标项（取 end 之前最后一条采集）"""
    row = conn.execute(
        "SELECT * FROM metric WHERE equipment_id = ? AND ts < ? "
        "ORDER BY ts DESC, rowid DESC LIMIT 1",
        (equipment_id, end_s),
    ).fetchone()
    if not row:
        return []
    metric = dict(row)
    return [
        key for key, limit in db.THRESHOLDS.items()
        if metric.get(key) and metric[key] > limit
    ]


def collect(at: datetime | None = None, ended: bool = False) -> dict:
    """汇总一个班次的巡检简报（纯读，返回可 JSON 序列化的 dict）

    所有「当前」口径都锚在班次结束时刻 end，不是「此刻」：
    常驻推送时两者相等，补发历史班次时才不会把今天的状态套到昨天头上。
    """
    start, end, name, is_ended = shift_window(at, ended)
    s, e = _fmt(start), _fmt(end)

    equip = {item["equipment_id"]: item for item in db.query_equipment()}

    with db.get_db() as conn:
        statuses = _statuses_as_of(conn, e)
        agg = conn.execute(
            "SELECT equipment_id, COUNT(*) AS samples, "
            "MAX(temp) AS max_temp, MAX(vibration) AS max_vibration, "
            "MAX(pressure) AS max_pressure "
            "FROM metric WHERE ts >= ? AND ts < ? GROUP BY equipment_id",
            (s, e),
        ).fetchall()
        samples = sum(row["samples"] for row in agg)

        devices_over: list[dict] = []
        for row in agg:
            eq_id = row["equipment_id"]
            items = []
            for key, (label, unit) in METRIC_META.items():
                peak = row[f"max_{key}"]
                limit = db.THRESHOLDS[key]
                if peak is None or peak <= limit:
                    continue
                peak_row = conn.execute(
                    f"SELECT ts FROM metric WHERE equipment_id = ? AND ts >= ? AND ts < ? "
                    f"AND {key} > ? ORDER BY {key} DESC, ts ASC LIMIT 1",
                    (eq_id, s, e, limit),
                ).fetchone()
                items.append(
                    {
                        "metric": key,
                        "label": label,
                        "unit": unit,
                        "peak": round(float(peak), 2),
                        "threshold": limit,
                        "at": peak_row["ts"] if peak_row else None,
                    }
                )
            if not items:
                continue
            devices_over.append(
                {
                    "equipment_id": eq_id,
                    "name": equip.get(eq_id, {}).get("name", ""),
                    "items": items,
                    "over_now": _over_at(conn, eq_id, e),
                    "open_work_orders": 0,
                }
            )
        # 当前仍越阈的排前面，其次当班越阈项多的 —— 收班时先看还在烧的那台
        devices_over.sort(
            key=lambda d: (-len(d["over_now"]), -len(d["items"]), d["equipment_id"])
        )

        wo_rows = [dict(r) for r in conn.execute("SELECT * FROM work_order").fetchall()]
        closed_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT wo_id, MIN(ts) AS ts FROM work_order_log "
                "WHERE action = 'status_change' AND detail LIKE '%→ closed%' "
                "AND ts >= ? AND ts < ? GROUP BY wo_id ORDER BY ts",
                (s, e),
            ).fetchall()
        ]
        knowledge_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT id, fault_code, symptom, source_wo_id, created_at FROM knowledge "
                "WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
                (s, e),
            ).fetchall()
        ]

    open_wos = []
    created_count = 0
    for wo in wo_rows:
        created_at = wo.get("created_at") or ""
        if s <= created_at < e:
            created_count += 1
        if created_at >= e:  # 收班之后才建的工单，不属于这一班
            continue
        status = statuses.get(wo["wo_id"], "open")
        if status == "closed":
            continue
        info = equip.get(wo["equipment_id"], {})
        age = _minutes_between(created_at, end)
        open_wos.append(
            {
                "wo_id": wo["wo_id"],
                "equipment_id": wo["equipment_id"],
                "equipment": f"{wo['equipment_id']} {info.get('name', '')}".strip(),
                "fault_desc": wo.get("fault_desc") or "",
                "severity": wo.get("severity") or "medium",
                "status": status,
                "status_label": feishu.STATUS_LABEL.get(status, status),
                "assignee": wo.get("assignee"),
                "created_at": created_at,
                "age_minutes": age,
                "overdue": age > SHIFT_HOURS * 60,
            }
        )
    # 挂得最久的排最前：简报的意义就是把最该催的那张顶到眼前
    open_wos.sort(key=lambda w: (-w["age_minutes"], w["wo_id"]))

    # 每台设备的挂单数按班次结束时的状态统计，与上面的未闭环列表同一口径
    per_device: dict[str, int] = {}
    for wo in open_wos:
        per_device[wo["equipment_id"]] = per_device.get(wo["equipment_id"], 0) + 1
    for device in devices_over:
        device["open_work_orders"] = per_device.get(device["equipment_id"], 0)

    summary = {
        "devices_total": len(equip),
        "samples": samples,
        "devices_over": len(devices_over),
        "devices_over_now": sum(1 for d in devices_over if d["over_now"]),
        "wo_open": len(open_wos),
        "wo_open_high": sum(1 for w in open_wos if w["severity"] == "high"),
        "wo_created": created_count,
        "wo_closed": len(closed_rows),
        "knowledge_added": len(knowledge_rows),
    }

    return {
        "shift": name,
        "window": {
            "start": s,
            "end": e,
            "hours": SHIFT_HOURS,
            "ended": is_ended,
            "label": f"{name} {start.strftime('%H:%M')}–{end.strftime('%H:%M')}",
            "span": f"{start.strftime('%m-%d %H:%M')} → {end.strftime('%m-%d %H:%M')}",
        },
        "generated_at": db.now(),
        "summary": summary,
        "devices_over": devices_over,
        "open_work_orders": open_wos,
        "closed_work_orders": [
            {"wo_id": r["wo_id"], "closed_at": r["ts"]} for r in closed_rows
        ],
        "knowledge": [
            {
                "id": r["id"],
                "fault_code": r["fault_code"],
                "symptom": r["symptom"],
                "source_wo_id": r.get("source_wo_id"),
            }
            for r in knowledge_rows
        ],
    }


def _strip_md(text: str) -> str:
    """webhook 文本通道没有富文本，把 ** 去掉，别让群消息里飘着一堆星号"""
    return text.replace("**", "")


def render_sections(report: dict) -> list[str]:
    """把简报拆成几块 markdown，卡片按块渲染（比一整块更好读）"""
    w, sm = report["window"], report["summary"]
    sections: list[str] = []
    now_word = "收班时" if w["ended"] else "此刻"

    head = [
        f"**{w['label']}**　{w['span']}"
        + ("" if w["ended"] else "（当班至今）"),
        f"设备 {sm['devices_total']} 台 · 采集 {sm['samples']:,} 点 · "
        f"越阈 {sm['devices_over']} 台（{now_word}仍越阈 {sm['devices_over_now']} 台）",
        f"工单：当班新建 {sm['wo_created']} 张 / 闭环 {sm['wo_closed']} 张 / "
        f"未闭环 {sm['wo_open']} 张"
        + (f"（其中紧急 {sm['wo_open_high']} 张）" if sm["wo_open_high"] else ""),
    ]
    if sm["samples"] == 0:
        head.append("⚠ 当班无采集数据，请检查工况模拟器/采集网关是否在跑")
    sections.append("\n".join(head))

    if report["devices_over"]:
        lines = ["**⚠ 当班越阈设备**"]
        for d in report["devices_over"]:
            parts = []
            for item in d["items"]:
                when = f"，{item['at'][11:16]} 峰值" if item["at"] else ""
                parts.append(
                    f"{item['label']}峰值 {item['peak']} {item['unit']}"
                    f"（阈 {item['threshold']}{when}）"
                )
            tail = f"　**{now_word}仍越阈**" if d["over_now"] else "　已回落"
            lines.append(f"- **{d['equipment_id']}** {d['name']}：{'；'.join(parts)}{tail}")
        sections.append("\n".join(lines))
    else:
        sections.append("**⚠ 当班越阈设备**\n无，全设备指标在阈值内 ✅")

    if report["open_work_orders"]:
        lines = [f"**🛠 未闭环工单（{sm['wo_open']}）**"]
        for wo in report["open_work_orders"]:
            sev = feishu.SEVERITY_EMOJI.get(wo["severity"], wo["severity"])
            who = f"　{wo['assignee']}" if wo["assignee"] else ""
            overdue = "　**超 12h 未闭环**" if wo["overdue"] else ""
            lines.append(
                f"- {wo['wo_id']}｜{wo['equipment']}｜{wo['status_label']}｜{sev}"
                f"｜已挂 {wo['age_minutes'] // 60}h{wo['age_minutes'] % 60}m{who}{overdue}"
            )
        sections.append("\n".join(lines))
    else:
        sections.append("**🛠 未闭环工单**\n无，当班工单均已闭环 ✅")

    if report["knowledge"]:
        lines = [f"**📚 当班沉淀经验（{len(report['knowledge'])}）**"]
        for kn in report["knowledge"][:8]:
            lines.append(f"- {kn['id']}　{kn['fault_code']}　{kn['symptom']}")
        if len(report["knowledge"]) > 8:
            lines.append(f"- …另有 {len(report['knowledge']) - 8} 条")
        sections.append("\n".join(lines))

    return sections


def render_markdown(report: dict) -> str:
    return "\n\n".join(render_sections(report))


def render_text(report: dict) -> str:
    """webhook 通道（自定义机器人）的降级纯文本"""
    w = report["window"]
    return f"📊 {w['label']} 巡检简报\n{w['span']}\n\n" + _strip_md(
        render_markdown(report)
    )


def build_shift_card(report: dict) -> dict:
    """飞书卡片 2.0：无按钮（简报是给人看的，动作在各自的工单卡片上）"""
    sm, w = report["summary"], report["window"]
    if sm["devices_over_now"]:
        template = "red"
    elif sm["devices_over"] or sm["wo_open"]:
        template = "orange"
    else:
        template = "green"
    title = f"📊 {report['shift']}巡检简报 · {w['start'][5:10].replace('-', '/')}"
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "body": {
            "elements": [{"tag": "markdown", "content": s} for s in render_sections(report)]
        },
    }


def send_shift_report(
    at: datetime | None = None, ended: bool = False, dry_run: bool = False
) -> tuple[bool, str, dict]:
    """生成并推送简报，返回 (成功?, 说明, 简报)。任何异常都不外抛，不阻断守护进程。"""
    report = collect(at, ended)
    if dry_run:
        return True, "dry-run 未推送", report
    try:
        if feishu.app_bot_enabled():
            ok, note = feishu.send_card_via_app(build_shift_card(report))
        elif feishu.feishu_enabled():
            ok, note = feishu.send_text(render_text(report))
        else:
            return False, "未配置飞书通道（APP 三件套或 webhook），跳过推送", report
    except Exception as e:  # noqa: BLE001 — 推送失败不能把定时器带崩
        return False, f"推送异常: {e}", report
    return ok, note, report


def next_boundary(at: datetime | None = None) -> datetime:
    """下一个班次边界（08:00 / 20:00），用于常驻模式计算休眠时长"""
    at = (at or datetime.now()).replace(microsecond=0)
    base = at.replace(hour=0, minute=0, second=0)
    for hour in (DAY_START_HOUR, NIGHT_START_HOUR):
        candidate = base.replace(hour=hour)
        if candidate > at:
            return candidate
    return (base + timedelta(days=1)).replace(hour=DAY_START_HOUR)


def run_daemon() -> None:
    print(
        f"[{db.now()}] 班次简报守护启动：每班收班（08:00 / 20:00）自动推送"
        + ("（自建应用）" if feishu.app_bot_enabled() else
           "（webhook）" if feishu.feishu_enabled() else "（未配置飞书，仅打印）"),
        flush=True,
    )
    while True:
        target = next_boundary()
        print(f"[{db.now()}] 下次推送：{_fmt(target)}", flush=True)
        # 分段睡眠：既能被 Ctrl+C 及时打断，也不会因系统休眠而漂掉整班
        while True:
            remain = (target - datetime.now()).total_seconds()
            if remain <= 0:
                break
            time.sleep(min(remain, 60))
        ok, note, report = send_shift_report(at=target, ended=True)
        sm = report["summary"]
        print(
            f"[{db.now()}] {report['shift']}简报 {'已推送' if ok else '未推送'}：{note}"
            f"（越阈 {sm['devices_over']} 台 / 未闭环 {sm['wo_open']} 张）",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="班次巡检简报")
    parser.add_argument("--send", action="store_true", help="推送到飞书（默认只打印）")
    parser.add_argument("--daemon", action="store_true", help="常驻：到点自动推送")
    parser.add_argument("--at", default=None, help="班次边界时刻，如 '2026-11-07 20:00'")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非 markdown")
    args = parser.parse_args()

    if args.daemon:
        try:
            run_daemon()
        except KeyboardInterrupt:
            print("\n已停止班次简报守护。")
        return

    at = parse_at(args.at)
    if args.send:
        ok, note, report = send_shift_report(at, ended=True)
        print(f"[{db.now()}] {report['shift']}简报 {report['window']['span']}")
        print(render_markdown(report))
        print(f"\n→ {'已推送' if ok else '未推送'}：{note}")
        sys.exit(0 if ok else 1)

    report = collect(at, ended=True)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))


if __name__ == "__main__":
    sys.exit(main())
