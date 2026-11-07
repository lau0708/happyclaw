/**
 * 报警卡片「接单 / 已处理 / 确认闭环」按钮的回写链路。
 *
 * 卡片本身由 mes-mcp-server 通过飞书自建应用发出，按钮 value 只携带
 * `{ action: 'mes_work_order', wo_id, op }` —— 卡片里不存业务状态，
 * 状态机的唯一真相仍然是 mes-mcp-server 的 SQLite（db.py 的 WORK_ORDER_TRANSITIONS）。
 *
 * 点击后 PulseOps 做三件事：
 *   1. 把点击翻译成一次 POST /api/work_orders/{id}/{op}（operator 用点击人姓名）
 *   2. 用 MES 返回的最新工单重建卡片 —— 卡片内容永远是库里的真实状态
 *   3. 返回 toast + 新卡片，飞书就地替换这条消息
 *
 * 两个约束决定了这里的写法：
 *   - 飞书要求回调 3s 内响应，否则按钮显示失败并重试，所以 HTTP 预算压到 2.2s；
 *   - 写操作必须幂等安全：重复点击会撞上状态机 409，直接把它翻译成人话。
 */
export const MES_WORK_ORDER_ACTION = 'mes_work_order';

export type MesWorkOrderOp = 'assign' | 'done' | 'close';

export interface MesWorkOrderPayload {
  action: typeof MES_WORK_ORDER_ACTION;
  wo_id: string;
  op: MesWorkOrderOp;
}

export const DEFAULT_MES_API_BASE = 'http://127.0.0.1:8002';
/** 飞书卡片回调的硬上限是 3s，留 0.8s 给网络与重建卡片。 */
export const MES_CALLBACK_BUDGET_MS = 2_200;

const OPS: ReadonlySet<string> = new Set(['assign', 'done', 'close']);

export interface MesWorkOrderRecord {
  wo_id: string;
  equipment_id: string;
  fault_desc?: string | null;
  severity?: string | null;
  status: string;
  assignee?: string | null;
  plan?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface CardActionToast {
  type: 'success' | 'error' | 'info';
  content: string;
}

export interface CardActionReply {
  toast: CardActionToast;
  /** `raw` 表示直接替换整张卡片；改不动卡片时省略。 */
  card?: { type: 'raw'; data: unknown };
}

/** 从按钮 value 读出一次工单动作；不是本项目发出的按钮返回 null。 */
export function parseMesWorkOrderValue(
  value: unknown,
): MesWorkOrderPayload | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  if (record.action !== MES_WORK_ORDER_ACTION) return null;
  const woId = typeof record.wo_id === 'string' ? record.wo_id.trim() : '';
  const op = typeof record.op === 'string' ? record.op.trim() : '';
  if (!woId || !OPS.has(op)) return null;
  return { action: MES_WORK_ORDER_ACTION, wo_id: woId, op: op as MesWorkOrderOp };
}

const STATUS_LABEL: Record<string, string> = {
  open: '待接单',
  assigned: '已接单',
  done: '已处理，待闭环',
  closed: '已闭环',
};

const SEVERITY_LABEL: Record<string, string> = {
  high: '🔴 紧急',
  medium: '🟡 一般',
  low: '🟢 低优',
};

const NEXT_ACTION: Record<
  string,
  { op: MesWorkOrderOp; label: string } | undefined
> = {
  open: { op: 'assign', label: '接单' },
  assigned: { op: 'done', label: '已处理' },
  done: { op: 'close', label: '确认闭环' },
};

/** 卡片上的下一步按钮；已闭环不再给按钮。 */
export function nextWorkOrderAction(
  status: string,
): { op: MesWorkOrderOp; label: string } | undefined {
  return NEXT_ACTION[status];
}

function clip(text: string, limit: number): string {
  const trimmed = text.trim();
  return trimmed.length > limit ? `${trimmed.slice(0, limit - 1)}…` : trimmed;
}

/** 卡片正文里的 markdown 内容不能把 @所有人 带出去。 */
function safeInline(text: string, limit = 300): string {
  return clip(text, limit).replace(/<at\b/gi, '&lt;at').replace(/\r?\n/g, ' ');
}

/**
 * 重建工单卡片（飞书卡片 2.0）。字段一律来自 MES 返回的工单行，
 * 因此卡片显示与看板、数据库永远一致。
 */
export function buildMesWorkOrderCard(input: {
  wo: MesWorkOrderRecord;
  equipmentName?: string;
}): unknown {
  const { wo } = input;
  const severity = (wo.severity || 'medium').toLowerCase();
  const equipment = input.equipmentName
    ? `${wo.equipment_id} ${input.equipmentName}`
    : wo.equipment_id;
  const statusText = STATUS_LABEL[wo.status] || wo.status;
  const elements: Array<Record<string, unknown>> = [
    { tag: 'markdown', content: `**设备**：${safeInline(equipment, 120)}` },
    {
      tag: 'markdown',
      content: `**问题**：${safeInline(wo.fault_desc || '巡检发现指标超限', 220)}`,
    },
    {
      tag: 'markdown',
      content: `**处置**：${safeInline(wo.plan || '待回填处置结果', 220)}`,
    },
    { tag: 'hr' },
    {
      tag: 'markdown',
      content:
        `**状态**：${statusText}` +
        (wo.assignee ? `　**处理人**：${safeInline(wo.assignee, 40)}` : ''),
    },
  ];

  const next = nextWorkOrderAction(wo.status);
  if (next) {
    elements.push({
      tag: 'button',
      text: { tag: 'plain_text', content: next.label },
      type: 'primary',
      behaviors: [
        {
          type: 'callback',
          value: { action: MES_WORK_ORDER_ACTION, wo_id: wo.wo_id, op: next.op },
        },
      ],
    });
  }

  return {
    schema: '2.0',
    config: { update_multi: true },
    header: {
      title: {
        tag: 'plain_text',
        content: `📋 ${SEVERITY_LABEL[severity] || severity} | 设备异常工单 ${wo.wo_id}`,
      },
      template: severity === 'high' ? 'red' : 'orange',
    },
    body: { elements },
  };
}

/** 处置回填里的时间戳用本地时间：工单日志是给人看的，不该出现 UTC 偏移。 */
function localStamp(now: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ` +
    `${pad(now.getHours())}:${pad(now.getMinutes())}`
  );
}

function actionBody(
  op: MesWorkOrderOp,
  operator: string,
  now: Date,
): Record<string, unknown> {
  if (op === 'assign') return { operator };
  if (op === 'done') {
    return {
      operator,
      resolution: `${operator} 于 ${localStamp(now)} 通过飞书卡片确认现场处置完成（细节可在看板补充）。`,
    };
  }
  return { operator };
}

const SUCCESS_TOAST: Record<MesWorkOrderOp, string> = {
  assign: '已接单，工单进入处置中。',
  done: '已回填处置结果，等待闭环确认。',
  close: '工单已闭环，处置经验已归档。',
};

function failureToast(status: number, message: string): string {
  if (status === 404) return '工单不存在或已被清理，请刷新看板。';
  if (status === 409) return message ? `工单状态已变化：${message}` : '工单状态已变化，请刷新看板。';
  if (status === 400) return message || '提交内容不完整，请在设备看板处理。';
  return message ? `工单系统返回异常：${message}` : '工单系统返回异常，请稍后重试。';
}

function safeJson(text: string): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function errorMessageOf(payload: unknown): string {
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>;
    if (typeof record.error === 'string') return record.error;
  }
  return '';
}

export interface MesActionDeps {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  now?: () => Date;
}

async function getJson(
  url: string,
  doFetch: typeof fetch,
  timeoutMs: number,
): Promise<unknown> {
  const response = await doFetch(url, {
    method: 'GET',
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) return undefined;
  return safeJson(await response.text().catch(() => ''));
}

function equipmentNameOf(payload: unknown, equipmentId: string): string | undefined {
  if (!Array.isArray(payload)) {
    if (payload && typeof payload === 'object') {
      const row = payload as Record<string, unknown>;
      return typeof row.name === 'string' ? row.name : undefined;
    }
    return undefined;
  }
  for (const item of payload) {
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    if (row.equipment_id === equipmentId && typeof row.name === 'string') {
      return row.name;
    }
  }
  return undefined;
}

/** 用一行工单拼出可替换的卡片；取不到设备名就退化成只显示编码。 */
async function cardFor(
  wo: MesWorkOrderRecord,
  baseUrl: string,
  doFetch: typeof fetch,
  timeoutMs: number,
): Promise<CardActionReply['card']> {
  let equipmentName: string | undefined;
  try {
    equipmentName = equipmentNameOf(
      await getJson(
        `${baseUrl}/api/equipment?equipment_id=${encodeURIComponent(wo.equipment_id)}`,
        doFetch,
        timeoutMs,
      ),
      wo.equipment_id,
    );
  } catch {
    equipmentName = undefined;
  }
  return { type: 'raw', data: buildMesWorkOrderCard({ wo, equipmentName }) };
}

/** 用库里的最新工单重建卡片；任何一步失败都只是“不给新卡片”，不改结论。 */
async function rebuildCard(
  woId: string,
  baseUrl: string,
  doFetch: typeof fetch,
  timeoutMs: number,
): Promise<CardActionReply['card']> {
  try {
    const wo = (await getJson(
      `${baseUrl}/api/work_orders/${encodeURIComponent(woId)}`,
      doFetch,
      timeoutMs,
    )) as MesWorkOrderRecord | undefined;
    if (!wo || typeof wo.status !== 'string') return undefined;
    return await cardFor(wo, baseUrl, doFetch, timeoutMs);
  } catch {
    return undefined;
  }
}

/**
 * 执行一次按钮动作并返回飞书回调响应。
 *
 * 成功与「状态已被别人改掉」都返回最新卡片：点击的人应该看到真实状态，
 * 而不是停留在自己点之前的那一版。
 */
export async function dispatchMesWorkOrderAction(
  payload: MesWorkOrderPayload,
  operatorName: string,
  deps: MesActionDeps = {},
): Promise<CardActionReply> {
  const baseUrl = (
    deps.baseUrl ??
    process.env.MES_API_URL ??
    DEFAULT_MES_API_BASE
  ).replace(/\/+$/, '');
  const doFetch = deps.fetchImpl ?? fetch;
  const timeoutMs = deps.timeoutMs ?? MES_CALLBACK_BUDGET_MS;
  const operator = clip(operatorName || '', 40) || '飞书用户';
  const now = deps.now ? deps.now() : new Date();

  let response: Response;
  try {
    response = await doFetch(
      `${baseUrl}/api/work_orders/${encodeURIComponent(payload.wo_id)}/${payload.op}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(actionBody(payload.op, operator, now)),
        signal: AbortSignal.timeout(timeoutMs),
      },
    );
  } catch {
    return {
      toast: {
        type: 'error',
        content: '工单系统没有响应，请稍后在设备看板处理。',
      },
    };
  }

  const body = safeJson(await response.text().catch(() => ''));

  if (!response.ok) {
    return {
      toast: {
        type: 'error',
        content: failureToast(response.status, errorMessageOf(body)),
      },
      card: await rebuildCard(payload.wo_id, baseUrl, doFetch, timeoutMs),
    };
  }

  const wo = body as MesWorkOrderRecord | undefined;
  const card =
    wo && typeof wo.status === 'string'
      ? await cardFor(wo, baseUrl, doFetch, timeoutMs)
      : await rebuildCard(payload.wo_id, baseUrl, doFetch, timeoutMs);

  return {
    toast: { type: 'success', content: SUCCESS_TOAST[payload.op] },
    ...(card ? { card } : {}),
  };
}
