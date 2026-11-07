"""init_db.py — 初始化模拟 MES 数据库（建表 + 种子数据），可重复运行（幂等清空重建）"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from db import DB_PATH, get_db, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS equipment (
  equipment_id   TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  type           TEXT,
  line           TEXT,
  status         TEXT DEFAULT 'running',
  install_date   TEXT,
  mfr            TEXT,
  model          TEXT
);

CREATE TABLE IF NOT EXISTS metric (
  id            TEXT PRIMARY KEY,
  equipment_id  TEXT NOT NULL REFERENCES equipment(equipment_id),
  ts            TEXT NOT NULL,
  temp          REAL,
  vibration     REAL,
  pressure      REAL,
  rpm           REAL,
  run_hours     INTEGER
);

CREATE TABLE IF NOT EXISTS history_fault (
  fault_id      TEXT PRIMARY KEY,
  equipment_id  TEXT NOT NULL REFERENCES equipment(equipment_id),
  fault_code    TEXT,
  fault_desc    TEXT,
  root_cause    TEXT,
  resolution    TEXT,
  occurred_at   TEXT,
  down_minutes  INTEGER
);

CREATE TABLE IF NOT EXISTS work_order (
  wo_id         TEXT PRIMARY KEY,
  equipment_id  TEXT NOT NULL REFERENCES equipment(equipment_id),
  fault_desc    TEXT,
  severity      TEXT DEFAULT 'medium',
  status        TEXT DEFAULT 'open',
  assignee      TEXT,
  plan          TEXT,
  created_at    TEXT DEFAULT (datetime('now')),
  updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS work_order_log (
  log_id    TEXT PRIMARY KEY,
  wo_id     TEXT NOT NULL REFERENCES work_order(wo_id),
  action    TEXT,
  detail    TEXT,
  operator  TEXT,
  ts        TEXT DEFAULT (datetime('now'))
);

-- 一台设备最多一张未闭环单：绕过 db.create_work_order 直插也插不进来
CREATE UNIQUE INDEX IF NOT EXISTS ux_work_order_open
  ON work_order(equipment_id) WHERE status IN ('open','assigned','done');

CREATE TABLE IF NOT EXISTS knowledge (
  id            TEXT PRIMARY KEY,
  fault_code    TEXT,
  symptom       TEXT,
  root_cause    TEXT,
  resolution    TEXT,
  source_wo_id  TEXT REFERENCES work_order(wo_id),
  created_at    TEXT DEFAULT (datetime('now'))
);
"""


def seed(conn) -> None:
    # 设备台账
    equipments = [
        ("CNC-01", "立式加工中心#1", "CNC", "机加工A线", "running", "2021-03-15", "MAZAK", "VCN-530"),
        ("CNC-03", "卧式加工中心#3", "CNC", "机加工A线", "running", "2020-11-02", "MAZAK", "HCN-6800"),
        ("INJ-01", "注塑机#1", "Injection", "注塑B线", "running", "2019-06-20", "Haitian", "MA2500"),
        ("ASM-01", "装配线#1", "Assembly", "总装C线", "running", "2022-01-10", "Siemens", "ASM-L1"),
    ]
    conn.executemany(
        "INSERT INTO equipment(equipment_id,name,type,line,status,install_date,mfr,model) "
        "VALUES (?,?,?,?,?,?,?,?)", equipments
    )

    # 运行指标（最近 72 小时，CNC-03 最新一条振动超标，用于触发演示报警）
    now_ts = datetime.now()
    rows = []
    for h in range(72, -1, -1):
        ts = (now_ts - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")
        # CNC-03 最近 6 小时振动逐步超限
        vib = 7.1 if h > 6 else round(8.5 + (6 - h) * 0.7, 2)
        rows.append((f"M-CNC03-{h}", "CNC-03", ts, 61.0 + h * 0.1, vib, 4.1, 3200, 12800))
        rows.append((f"M-CNC01-{h}", "CNC-01", ts, 58.0, 6.2, 4.0, 3100, 14200))
    conn.executemany(
        "INSERT INTO metric(id,equipment_id,ts,temp,vibration,pressure,rpm,run_hours) "
        "VALUES (?,?,?,?,?,?,?,?)", rows
    )

    # 历史故障（CNC-03 主轴轴承问题，已沉淀知识）
    faults = [
        ("F-001", "CNC-03", "SPINDLE-BEARING", "主轴异响、振动超限", "主轴轴承磨损",
         "更换主轴轴承并校准动平衡", (now_ts - timedelta(days=21)).strftime("%Y-%m-%d %H:%M:%S"), 96),
        ("F-002", "CNC-03", "COOLANT-LEAK", "冷却液泄漏、温度偏高", "冷却管路接头老化",
         "更换接头并补充冷却液", (now_ts - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S"), 35),
        ("F-003", "INJ-01", "PRESSURE-DROP", "注塑压力不足", "液压泵压力阀卡滞",
         "清洗压力阀芯并重调压力", (now_ts - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S"), 60),
    ]
    conn.executemany(
        "INSERT INTO history_fault(fault_id,equipment_id,fault_code,fault_desc,root_cause,"
        "resolution,occurred_at,down_minutes) VALUES (?,?,?,?,?,?,?,?)", faults
    )

    # 预置知识（供经验回流命中）
    conn.executemany(
        "INSERT INTO knowledge(id,fault_code,symptom,root_cause,resolution,source_wo_id,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("KN-001", "SPINDLE-BEARING", "主轴振动超限 主轴异响", "主轴轴承磨损",
             "停机检查→测轴承游隙→更换主轴轴承并校准动平衡（约90分钟）", "WO-20260915-001",
             (now_ts - timedelta(days=21)).strftime("%Y-%m-%d %H:%M:%S")),
            ("KN-002", "COOLANT-LEAK", "冷却液泄漏 温度偏高", "冷却管路接头老化",
             "更换接头→补充冷却液→复测温度", "WO-20260928-001",
             (now_ts - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")),
        ],
    )


def main() -> None:
    conn = get_db()
    # 幂等：清空重建
    for table in ("work_order_log", "work_order", "knowledge",
                  "history_fault", "metric", "equipment"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.executescript(SCHEMA)
    seed(conn)
    conn.commit()
    conn.close()
    print(f"✔ 初始化完成，数据库已写入: {DB_PATH}")


if __name__ == "__main__":
    sys.exit(main())
