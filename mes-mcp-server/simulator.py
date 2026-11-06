"""simulator.py — 车间工况模拟器（C 方案）

解决什么问题：
  init_db.py 造的种子数据是"冻结"的——146 条指标停在初始化那一刻，
  看板曲线不再生长，巡检永远读到同一个最新值。
  本脚本按「基线 + 日周期负载 + 随机噪声 + 缓慢劣化」生成连续数据，
  并走真实采集入口 POST /api/metric/batch 上报（与将来网关走同一条路），
  因此前端、巡检、诊断、建单、飞书推送全链路都会被真实驱动。

用法：
    python3 simulator.py --once                    # 生成一轮（每台设备 1 条）
    python3 simulator.py --interval 10             # 每 10 秒一轮（持续，演示用）
    python3 simulator.py --backfill 24             # 回填最近 24 小时（5 分钟粒度）后退出
    python3 simulator.py --backfill 24 --interval 10   # 先补历史，再持续跑
    python3 simulator.py --no-fault                # 关闭故障注入（全部设备保持正常）
    python3 simulator.py --direct                  # 跳过 HTTP，直接写库（离线调试）
    python3 simulator.py --dry-run                 # 只打印，不写库

故障注入：默认让 CNC-03 的振动按 6 mm/s 每小时缓慢爬升（演示节奏，约 9 分钟
越过 8.0 阈值触发闭环）。真实轴承磨损可取 --fault-rate 0.3（约 3 小时爬升 1 mm/s）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402

db.load_env()

# 按设备类型的工况基线（可从真实台账逐步替换为实测均值）
BASE_BY_TYPE: dict[str, dict[str, float]] = {
    "CNC": {"temp": 58.0, "vibration": 6.4, "pressure": 4.0, "rpm": 3100},
    "Injection": {"temp": 69.0, "vibration": 5.2, "pressure": 5.0, "rpm": 180},
    "Assembly": {"temp": 42.0, "vibration": 2.4, "pressure": 0.6, "rpm": 900},
}
DEFAULT_BASE = {"temp": 50.0, "vibration": 4.0, "pressure": 3.0, "rpm": 1500}

# 噪声标准差：温度波动大、振动要求精度高
NOISE = {"temp": 0.45, "vibration": 0.12, "pressure": 0.05, "rpm": 25.0}


def _clamp(field_name: str, value: float) -> float:
    low, high = db.METRIC_RANGES[field_name]
    return max(low, min(high, value))


@dataclass
class DeviceModel:
    """单台设备的工况模型：基线 + 日周期 + 噪声 + 劣化漂移"""

    equipment_id: str
    base: dict[str, float]
    rng: random.Random
    run_hours: float = 0.0
    drift: dict[str, float] = field(
        default_factory=lambda: {"temp": 0.0, "vibration": 0.0, "pressure": 0.0}
    )
    fault_field: str | None = None
    fault_rate: float = 0.0

    def sample(self, ts: datetime, dt_hours: float, apply_fault: bool = True) -> dict:
        """生成一个时间点的四项指标（未带 equipment_id / ts）"""
        hour = ts.hour + ts.minute / 60 + ts.second / 3600
        # 日周期：凌晨 6 点负载最低，傍晚 18 点最高（车间两班倒的典型形状）
        daily = math.sin(2 * math.pi * (hour - 6) / 24)
        load = 1.0 + 0.05 * daily

        if apply_fault and self.fault_field and self.fault_rate:
            self.drift[self.fault_field] += self.fault_rate * dt_hours
        self.run_hours += dt_hours

        values = {
            "temp": self.base["temp"] + 2.0 * daily
            + self.rng.gauss(0, NOISE["temp"]) + self.drift["temp"],
            "vibration": self.base["vibration"] + 0.25 * daily
            + self.rng.gauss(0, NOISE["vibration"]) + self.drift["vibration"],
            "pressure": self.base["pressure"] + 0.05 * daily
            + self.rng.gauss(0, NOISE["pressure"]) + self.drift["pressure"],
            "rpm": self.base["rpm"] * load + self.rng.gauss(0, NOISE["rpm"]),
        }
        return {
            "temp": round(_clamp("temp", values["temp"]), 1),
            "vibration": round(_clamp("vibration", values["vibration"]), 2),
            "pressure": round(_clamp("pressure", values["pressure"]), 2),
            "rpm": int(round(_clamp("rpm", values["rpm"]))),
            "run_hours": int(self.run_hours),
        }


class Simulator:
    """把设备台账实例化为一组工况模型"""

    def __init__(
        self,
        equipment: list[dict],
        seed: int = 42,
        fault: str | None = None,
        fault_rate: float = 6.0,
    ) -> None:
        self.models: list[DeviceModel] = []
        fault_target: tuple[str, str] | None = None
        if fault:
            eq_id, _, field_name = fault.partition(":")
            if field_name not in ("temp", "vibration", "pressure"):
                raise ValueError(f"故障字段非法: {field_name}（可选 temp/vibration/pressure）")
            fault_target = (eq_id.strip(), field_name)

        for item in equipment:
            eq_id = item["equipment_id"]
            base = dict(BASE_BY_TYPE.get(item.get("type") or "", DEFAULT_BASE))
            # 同型号不同机台之间本来就存在个体差异，用设备号做确定性微扰
            jitter = random.Random(f"{seed}-{eq_id}")
            base = {k: v * (1 + jitter.uniform(-0.03, 0.03)) for k, v in base.items()}

            last = db.latest_metric_row(eq_id)
            run_hours = float(last.get("run_hours") or 0) if last else jitter.uniform(8000, 15000)

            is_fault = fault_target is not None and fault_target[0] == eq_id
            self.models.append(
                DeviceModel(
                    equipment_id=eq_id,
                    base=base,
                    rng=random.Random(f"{seed}-{eq_id}-noise"),
                    run_hours=run_hours,
                    fault_field=fault_target[1] if is_fault else None,
                    fault_rate=fault_rate if is_fault else 0.0,
                )
            )

    def sample_all(
        self, ts: datetime, dt_hours: float, apply_fault: bool = True
    ) -> list[dict]:
        """一个时间点、所有设备各一条上报负载"""
        rows = []
        for model in self.models:
            payload = model.sample(ts, dt_hours, apply_fault=apply_fault)
            payload["equipment_id"] = model.equipment_id
            payload["ts"] = ts.strftime("%Y-%m-%d %H:%M:%S")
            rows.append(payload)
        return rows


# --------------------------------------------------------------------------- #
# 上报通道
# --------------------------------------------------------------------------- #
class HttpSender:
    """走真实采集入口：POST /api/metric/batch（与将来车间网关同一条路）"""

    def __init__(self, api_url: str, api_key: str = "", timeout: int = 10) -> None:
        self.url = api_url.rstrip("/") + "/api/metric/batch"
        self.api_key = api_key
        self.timeout = timeout

    def __call__(self, rows: list[dict]) -> dict:
        body = json.dumps({"metrics": rows}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


class DirectSender:
    """直连数据库（不依赖 API 进程，离线调试用）"""

    def __call__(self, rows: list[dict]) -> dict:
        accepted = duplicates = 0
        errors = []
        for row in rows:
            try:
                result = db.insert_metric(**row)
            except db.MetricError as e:
                errors.append(str(e))
                continue
            if result.get("duplicate"):
                duplicates += 1
            else:
                accepted += 1
        return {"accepted": accepted, "duplicates": duplicates, "rejected": len(errors)}


class DryRunSender:
    """只打印，不写库"""

    def __init__(self) -> None:
        self.total = 0

    def __call__(self, rows: list[dict]) -> dict:
        self.total += len(rows)
        for row in rows[:2]:
            print("   ", json.dumps(row, ensure_ascii=False))
        if len(rows) > 2:
            print(f"    … 共 {len(rows)} 条")
        return {"accepted": len(rows), "duplicates": 0, "rejected": 0}


def _summarize(result: dict) -> str:
    parts = [f"入库 {result.get('accepted', 0)}"]
    if result.get("duplicates"):
        parts.append(f"幂等跳过 {result['duplicates']}")
    if result.get("rejected"):
        parts.append(f"拒收 {result['rejected']}")
    return " · ".join(parts)


def _send(sender, rows: list[dict], verbose: bool = False) -> bool:
    try:
        result = sender(rows)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        print(f"✘ 上报被拒绝 HTTP {e.code}: {detail}")
        if e.code in (401, 403):
            print("   → 服务端启用了上报鉴权：请在 .env 设置 INGEST_API_KEY，"
                  "并让 simulator 用同一值（--api-key 或同名环境变量）")
            raise SystemExit(2)
        return False
    except urllib.error.URLError as e:
        print(f"✘ 连不上 API（{e.reason}）。请先启动：python3 api.py")
        return False
    if verbose:
        for row in rows:
            print(f"    {row['equipment_id']}  温度={row['temp']}  振动={row['vibration']}  "
                  f"压力={row['pressure']}  转速={row['rpm']}")
    print(f"✔ {datetime.now().strftime('%H:%M:%S')}  {_summarize(result)}")
    return True


def backfill(sim: Simulator, hours: float, step_seconds: int, sender, chunk: int = 200) -> None:
    """回填历史数据：不带故障注入，保持"故障从此刻开始"的演示叙事"""
    total_points = int(hours * 3600 / step_seconds)
    print(f"→ 回填最近 {hours:g} 小时，{step_seconds}s 粒度，约 {total_points} 个时间点")
    end = datetime.now()
    ts = end - timedelta(seconds=total_points * step_seconds)
    buffer: list[dict] = []
    sent = 0
    while ts <= end:
        buffer.extend(sim.sample_all(ts, step_seconds / 3600, apply_fault=False))
        if len(buffer) >= chunk:
            result = sender(buffer)
            sent += result.get("accepted", 0) + result.get("duplicates", 0)
            buffer = []
        ts += timedelta(seconds=step_seconds)
    if buffer:
        result = sender(buffer)
        sent += result.get("accepted", 0) + result.get("duplicates", 0)
    print(f"✔ 回填完成，共 {sent} 条")


def main() -> None:
    parser = argparse.ArgumentParser(description="车间工况模拟器：生成设备指标并上报")
    parser.add_argument("--interval", type=float, default=10.0, help="持续模式采样间隔（秒）")
    parser.add_argument("--once", action="store_true", help="只生成一轮后退出")
    parser.add_argument("--devices", default=None, help="设备编码，逗号分隔；默认台账全部")
    parser.add_argument("--backfill", type=float, default=0.0, help="回填小时数（0=不回填）")
    parser.add_argument("--backfill-interval", type=int, default=300, help="回填粒度（秒）")
    parser.add_argument("--api-url", default=os.environ.get("MES_API_URL", "http://127.0.0.1:8002"))
    parser.add_argument("--api-key", default=os.environ.get("INGEST_API_KEY", ""))
    parser.add_argument("--direct", action="store_true", help="直接写库，不走 HTTP")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写库")
    parser.add_argument("--fault", default="CNC-03:vibration", help="故障注入 EQ:field；空串关闭")
    parser.add_argument("--fault-rate", type=float, default=6.0, help="劣化速率（单位/小时）")
    parser.add_argument("--no-fault", action="store_true", help="关闭故障注入")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（保证可复现）")
    parser.add_argument("--verbose", action="store_true", help="逐台打印采样值")
    args = parser.parse_args()

    equipment = db.query_equipment()
    if args.devices:
        wanted = {d.strip() for d in args.devices.split(",") if d.strip()}
        equipment = [e for e in equipment if e["equipment_id"] in wanted]
        missing = wanted - {e["equipment_id"] for e in equipment}
        if missing:
            print(f"✘ 台账里没有这些设备: {', '.join(sorted(missing))}")
            raise SystemExit(2)
    if not equipment:
        print("✘ 设备台账为空，请先运行 python3 init_db.py")
        raise SystemExit(2)

    fault = None if args.no_fault else (args.fault or None)
    sim = Simulator(equipment, seed=args.seed, fault=fault, fault_rate=args.fault_rate)

    if args.dry_run:
        sender = DryRunSender()
    elif args.direct:
        sender = DirectSender()
    else:
        sender = HttpSender(args.api_url, args.api_key)

    mode = "dry-run" if args.dry_run else ("直接写库" if args.direct else f"HTTP → {args.api_url}")
    print(f"===== 工况模拟器 · {len(equipment)} 台设备 · 模式: {mode} =====")
    if fault:
        target_eq, _, field_name = fault.partition(":")
        model = next((m for m in sim.models if m.equipment_id == target_eq), None)
        if model is None:
            print(f"   故障注入目标 {target_eq} 不在本次模拟范围内，已忽略")
        else:
            start_value = model.base[field_name]
            threshold = db.THRESHOLDS[field_name]
            minutes = max(0.0, (threshold - start_value) / args.fault_rate * 60)
            print(f"   故障注入: {fault}，劣化 {args.fault_rate:g}/小时"
                  f"（基线 {start_value:.2f}，约 {minutes:.0f} 分钟后越过阈值 {threshold:g}）")

    if args.backfill > 0:
        backfill(sim, args.backfill, args.backfill_interval, sender)
        if args.once:
            return

    if args.once:
        _send(sender, sim.sample_all(datetime.now(), args.interval / 3600), args.verbose)
        return

    print(f"→ 持续采样：每 {args.interval:g}s 一轮（Ctrl+C 退出）")
    try:
        while True:
            started = time.monotonic()
            _send(sender, sim.sample_all(datetime.now(), args.interval / 3600), args.verbose)
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, args.interval - elapsed))
    except KeyboardInterrupt:
        print("\n已停止模拟。")


if __name__ == "__main__":
    sys.exit(main())
