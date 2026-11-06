# mcp-server-mes · 模拟 MES 脚手架

面向 **PulseOps 设备异常工单闭环 Agent** 的可运行脚手架：把模拟 MES 封装为 Agent 可调用的 MCP 工具，跑通「报警 → 诊断 → 工单 → 处置 → 复盘 → 经验回流」完整闭环。

## 目录结构

```
mes-mcp-server/
├── db.py          # 数据访问层（仅标准库 sqlite3）
├── init_db.py     # 建表 + 种子数据（幂等，可重复运行）
├── server.py      # MCP Server（FastMCP），供 PulseOps 接入
├── demo.py        # 端到端闭环演示（不依赖 mcp 包）
├── scheduler.py   # 定时巡检 Agent（模拟 PulseOps Cron，自动建单 + 幂等）
├── workorder_flow.py  # 工单闭环：经 MCP 协议跑通 建单→处置→复盘→经验回流
├── health_check.py    # 主动查询触发：人让 Agent 查设备健康状态并判定
├── api.py         # 轻量 HTTP JSON API（标准库）：看板取数 + 网关指标上报（/api/metric）
├── agent_nodes.py # LLM 节点：诊断 / 问诊解析（OpenAI 兼容端点，无 key 自动降级）
├── feishu.py      # 飞书群机器人推送（Webhook + 可选签名校验，未配置自动跳过）
├── simulator.py   # 车间工况模拟器：基线+日周期+噪声+劣化，走真实上报入口写库
├── test_state_machine.py  # 状态机 + 数据口径回归（15 用例）
├── test_closed_loop.py    # 闭环 Eval：幂等 / 经验回流 / 双触发收敛（8 用例）
├── test_agent_nodes.py    # LLM 节点单测（假传输层，不联网不耗 token）
├── test_ingest.py         # 采集入库 + 模拟器（29 用例，用临时库不污染演示数据）
├── web/           # Vue3 + TS 工单看板（Vite）
├── requirements.txt
├── README.md
└── data/          # 生成的 mes.db
```

## 快速开始（无需第三方依赖即可看闭环）

```bash
cd mes-mcp-server
python3 init_db.py     # 初始化模拟 MES 数据库
python3 demo.py        # 跑通完整闭环演示
python3 scheduler.py --once   # 模拟定时巡检：异常自动建单
python3 workorder_flow.py     # 经 MCP 协议走完 建单→处置→复盘→经验回流
python3 health_check.py       # 主动查询：人让 Agent 查设备健康判定（CNC-03 异常 / CNC-01 健康）
```

> `workorder_flow.py` / `health_check.py` 走**标准 MCP Client**（`pip install mcp`），
> 主动查询触发是闭环的另一种起点：由人先问 Agent 查设备，判定异常后进入同一诊断/工单流程。

## 定时巡检（模拟 PulseOps Cron）

```bash
python3 scheduler.py --once            # 单次巡检（演示）
python3 scheduler.py --interval 30     # 每 30 秒巡检，异常自动诊断+建单
python3 scheduler.py --devices CNC-03,INJ-01   # 指定设备
```
对同一设备未闭环工单做幂等去重，避免重复建单。真实 PulseOps 中由 Cron 定时任务 + 巡检 Agent 等价实现。

## 数据采集：真实设备数据怎么进来

页面的设备数据有两条写入路径，都汇到 `db.insert_metric()`（物理量校验 + 同秒幂等）：

**① 采集上报（对应真实车间的网关）**

```bash
# 单条上报
curl -X POST http://localhost:8002/api/metric \
  -H "Content-Type: application/json" \
  -d '{"equipment_id":"CNC-03","temp":61.2,"vibration":8.6,"pressure":4.1,"rpm":3250}'

# 批量补传（断网恢复后续传；坏点不阻断好点）
curl -X POST http://localhost:8002/api/metric/batch \
  -H "Content-Type: application/json" \
  -d '{"metrics":[{"equipment_id":"CNC-01","ts":"2026-11-06T10:00:00Z","vibration":6.2}, ...]}'
```

- 鉴权：`.env` 配置 `INGEST_API_KEY` 后，上报必须带同名请求头 `X-API-Key`；
  未配置时仅接受本机上报（403 提示），网关跨机部署务必配置
- 校验：设备编码必须已登记（404）、数值必须在物理区间内（400）、时间戳自动归一（兼容 ISO8601）
- 幂等：同设备同秒重复上报返回 `duplicate: true`，网关重试不会把点数翻倍

**② 工况模拟器（让看板"活"起来）**

`init_db.py` 的种子数据是冻结的；模拟器按「基线 + 日周期负载 + 随机噪声 + 缓慢劣化」
持续生成数据，并**走同一个上报端点**（与将来网关同一条路），前端/巡检/诊断/飞书全链路被真实驱动：

```bash
python3 simulator.py --backfill 24 --interval 10   # 回填 24h 历史 + 每 10s 持续采样
python3 simulator.py --once                        # 只生成一轮
python3 simulator.py --no-fault                    # 关闭故障注入
python3 simulator.py --direct                      # 跳过 HTTP 直连写库（离线调试）
make mes-sim ARGS="--interval 30"                  # Makefile 入口
```

默认注入 CNC-03 振动缓慢劣化（6 mm/s·h，约十几分钟越过 8.0 阈值），自动触发
巡检 → LLM 诊断 → 建单 → 飞书推送的完整闭环；`--fault-rate 0.3` 更接近真实磨损速度。

**③ 接入真实车间**：把网关/既有 MES 的数据对接到 ① 的上报端点即可，上层零改动；
若厂里已有 MES 数据库，也可直接改 `db.py` 的 `query_*` 查对方视图。

## 工单看板（Vue3 + TS）

后端 HTTP API（端口 8002，被占用时自动顺延到空闲端口）：
```bash
python3 api.py --port 8002
# 若 8002 被 Docker 等占用，会自动顺延（如 8003）并打印实际端口
```
前端看板（`MES_API_PORT` 需与后端实际端口一致）：
```bash
cd web
npm install
MES_API_PORT=8003 npm run dev   # http://localhost:5173，/api 代理到 8003
npm run build      # 生产构建
```
看板含设备概览（超限警示/未闭环工单数）、工单列表（状态筛选 + 闭环操作）、故障处置经验面板（知识回流）。

`demo.py` 用标准库模拟三个 Agent（数据 / 诊断 / 工单）的调用链，展示：
- ① 报警触发 → ② 数据Agent 拉设备上下文 → ③ 诊断Agent 命中知识
- ④ 工单Agent 建单并推送 → ⑤ 人工处置回填 → ⑥ 复盘沉淀
- 场景二再次报警时，**经验回流命中**历史处置方案（越用越准）。

## 接入 PulseOps（MCP Server）

```bash
pip install -r requirements.txt
python server.py --transport sse --host 0.0.0.0 --port 18080   # SSE，供 Docker 部署的 PulseOps 接入
# 或
python server.py                                               # stdio（PulseOps 直接跑在宿主机时用）
```

### Docker 部署的 PulseOps（Agent 跑在沙箱容器里）

PulseOps 的 Agent 在 **Docker 沙箱容器**内执行 MCP 工具，容器里看不到宿主机路径，
所以 **stdio 方式（command/args 指向宿主机文件）不可用**。改用 SSE：MCP 服务由宿主机提供，
容器经 `host.docker.internal` 发 HTTP 请求，不依赖容器内文件。

在 PulseOps「能力治理 → MCP」注册：

| 字段 | 值 |
|---|---|
| 名称 / ID | `mes-server` |
| 类型 | `sse` |
| URL | `http://host.docker.internal:18080/sse` |

⚠️ 两个坑（已处理，重跑时注意）：
1. **旧进程占端口**：重启前先 `lsof -iTCP:18080 -sTCP:LISTEN -P -n` 确认并 `kill -9`，否则新进程 `Errno 48` 起不来，Agent 仍连旧服务。
2. **Host 校验 421**：mcp 库 DNS rebinding 防护默认只信 `127.0.0.1/localhost`，`host.docker.internal` 会被 **421 拒绝**。`server.py` 已在构造 `FastMCP` 时用 `transport_security.allowed_hosts` 放行 `host.docker.internal:*`（见 `server.py` 顶部）。

注册后在智能体对应工作区启用，即可让数据 / 诊断 / 工单三个 Agent 调用以下工具：

| 工具 | 归属 Agent | 作用 |
|---|---|---|
| `query_equipment` | 数据Agent | 查设备台账 / 状态 |
| `query_metric` | 数据Agent | 查运行指标 |
| `query_history_faults` | 数据Agent | 查历史故障 / 处置（支持 fault_code 或 fault_id） |
| `search_knowledge` | 诊断Agent | 命中历史处置经验 |
| `create_work_order` | 工单Agent | 创建工单 |
| `update_work_order` | 工单Agent | 回填处置结果（经状态机校验） |
| `query_work_orders` | 工单Agent | 查工单列表（按状态/设备过滤，供看板） |
| `save_knowledge` | 诊断Agent | 沉淀经验（复盘） |
| `check_latest_metric` | 巡检 | 最新指标超阈值判定 |

## 工单状态机

`open → assigned → done → closed`（允许 `open → done` 直接回填；跳级 / 回退 / 终态再流转会被拒绝，
`update_work_order` 抛 `WorkOrderError`，HTTP API 关单端点返回 409）。看板「一键关单」不再绕过状态机，
而是沿合法路径补全中间环节并逐步写 `work_order_log`，全程可审计。

## LLM 诊断节点（agentic workflow）

确定性骨架（巡检/阈值/幂等/建单/状态机）保持普通代码，只在诊断节点引入模型：

```bash
export LLM_BASE_URL=https://api.deepseek.com/v1   # 任意 OpenAI 兼容端点
export LLM_API_KEY=sk-xxx
export LLM_MODEL=deepseek-chat
python3 scheduler.py --once   # 诊断走真实 LLM（事实包喂入，数字可对账）
```

- 未配置 `LLM_API_KEY` 时**自动降级**为知识库关键词命中，全链路零依赖可跑通
- 事实包（fact_pack）只含模型需要的数据：最新指标 + 超限项 + 3 条历史故障 + 知识命中，控制 token
- 模型输出强制 JSON，解析失败重试 1 次后降级，坏输出不进工单
- 问诊解析节点 `parse_inquiry()`：把「3号机振动不对，看下」解析成 equipment_id + 意图，路由进同一闭环

### 实测：LLM 模式 vs 知识库降级模式（deepseek-chat）

```
LLM  模式: 根因=主轴轴承磨损复发，导致振动值12.7 mm/s超过8.0阈值 | 置信度=0.85
           依据=最新振动值12.7超过阈值8.0；历史故障F-001同为SPINDLE-BEARING（停机96分钟）；
           知识库KN-001命中该故障码；温度61.0/压力4.1/转速3200未见异常；run_hours 12800已长期运行
降级 模式: 根因=主轴轴承磨损 | 置信度=0.8
           依据=知识库命中 KN-001（未配置 LLM，关键词命中降级）
```

对比结论：
- **LLM 模式**给出证据链推理（逐项排除温度/压力/转速）、量化依据（12.7 vs 8.0）、
  结合设备服役时长（run_hours 12800）佐证"磨损复发"，处置方案带复测闭环动作
- **降级模式**仅返回知识库原文，无推理、无法结合当次指标做排除判断
- 健康设备（CNC-01）LLM 正确输出"无异常证据，不编造根因"，置信度回落至 0.5——
  验证了「数字全部来自事实包，模型只做判断」的防幻觉约束有效
- LLM 调用失败/超时/key 无效时自动降级，巡检不中断

## 飞书报警推送

巡检建单后自动推送**工单报警卡片**到飞书群（设备 / 超限项 / 根因 / 处置方案 / 置信度）：

1. 飞书群里：`设置 → 群机器人 → 添加自定义机器人`，复制 Webhook 地址
2. 填入 `.env`（安全设置选了「签名校验」就同时填 secret）：
   ```
   FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
   FEISHU_WEBHOOK_SECRET=
   ```
3. `python3 scheduler.py --once` → 工单建好后群里收到红色报警卡片

未配置 webhook 时自动跳过推送，不影响巡检；推送失败也只告警不阻断。

## 回归测试（交付标准验收）

```bash
make mes-test    # 或逐个运行：
python3 init_db.py
python3 test_state_machine.py   # 状态机 9 + 数据契约 6
python3 test_closed_loop.py     # 幂等 / 经验回流 / 双触发收敛
python3 test_agent_nodes.py     # LLM 节点（假传输层，不联网）
python3 test_ingest.py          # 采集入库 + 模拟器（29 用例，临时库不污染演示数据）
```

## 数据模型

六张表：`equipment`（设备台账）、`metric`（运行指标）、`history_fault`（历史故障）、
`work_order`（工单）、`work_order_log`（处置日志）、`knowledge`（沉淀经验）。

## 演示闭环示意输出

```
① 事件触发：设备 CNC-03 最新振动值超阈值（>8.0 mm/s）
② 数据Agent：设备台账 + 历史故障 2 条
③ 诊断Agent：【经验回流命中】根因=主轴轴承磨损 处置=更换轴承并校准动平衡
④ 工单Agent：工单 WO-xxxx 已创建，已推送维修班
⑤ 人工处置：回填处置结果，工单 closed
⑥ 复盘沉淀：写入知识库，供下次同类报警直接命中
```

## 说明

- 种子/模拟数据为**演示**用途；接入真实设备时，让车间网关对接 `/api/metric` 上报端点，
  或替换 `db.py` 中的查询为真实 MES API，`server.py` 与 Agent 编排层无需改动。
- 指标类数值请在实测后填写，代码中阈值（振动 8.0 / 温度 75 / 压力 5.5）为示例。
