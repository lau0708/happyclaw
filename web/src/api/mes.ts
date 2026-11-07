/**
 * mes.ts — 对接自研 mcp-server-mes 的 HTTP API（经 vite /mes-api 代理 → api.py:8002）
 * 与 PulseOps 自己的 /api（后端 3000）隔离，避免冲突。
 */
export interface Equipment {
  equipment_id: string;
  name: string;
  type: string;
  line: string;
  status: string;
  install_date: string | null;
  mfr: string | null;
  model: string | null;
}

export interface Metric {
  id: string;
  equipment_id: string;
  ts: string;
  temp: number | null;
  vibration: number | null;
  pressure: number | null;
  rpm: number | null;
  run_hours: number | null;
}

export interface DeviceStatus {
  equipment_id: string;
  name: string;
  status: string;
  latest_metric: Metric | null;
  over_threshold: string[];
  open_work_orders: number;
}

export interface WorkOrder {
  wo_id: string;
  equipment_id: string;
  fault_desc: string;
  severity: string;
  status: string;
  assignee: string | null;
  plan: string | null;
  created_at: string;
  updated_at: string;
}

export interface Knowledge {
  id: string;
  fault_code: string;
  symptom: string;
  root_cause: string;
  resolution: string;
  source_wo_id: string | null;
  created_at: string;
}

export interface HistoryFault {
  fault_id: string;
  equipment_id: string;
  fault_code: string;
  fault_desc: string;
  root_cause: string;
  resolution: string;
  occurred_at: string;
  down_minutes: number;
}

export interface WorkOrderLog {
  action: string;
  detail: string;
  operator: string | null;
  ts: string;
}

/** 班次简报：与 mes-mcp-server/shift_report.py 的 collect() 返回结构一一对应 */
export interface ShiftReportOverItem {
  metric: string;
  label: string;
  unit: string;
  peak: number;
  threshold: number;
  at: string | null;
}

export interface ShiftReportDevice {
  equipment_id: string;
  name: string;
  items: ShiftReportOverItem[];
  /** 收班时刻仍在越阈的指标项 */
  over_now: string[];
  open_work_orders: number;
}

export interface ShiftReportOrder {
  wo_id: string;
  equipment_id: string;
  equipment: string;
  fault_desc: string;
  severity: string;
  status: string;
  status_label: string;
  assignee: string | null;
  created_at: string;
  age_minutes: number;
  overdue: boolean;
}

export interface ShiftReport {
  shift: string;
  window: {
    start: string;
    end: string;
    hours: number;
    ended: boolean;
    label: string;
    span: string;
  };
  generated_at: string;
  summary: {
    devices_total: number;
    samples: number;
    devices_over: number;
    devices_over_now: number;
    wo_open: number;
    wo_open_high: number;
    wo_created: number;
    wo_closed: number;
    knowledge_added: number;
  };
  devices_over: ShiftReportDevice[];
  open_work_orders: ShiftReportOrder[];
  closed_work_orders: { wo_id: string; closed_at: string }[];
  knowledge: { id: string; fault_code: string; symptom: string; source_wo_id: string | null }[];
}

const base = '/mes-api';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(base + path);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(base + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  const data = (await res.json().catch(() => ({}))) as T & { error?: string };
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}

export const mesApi = {
  deviceStatus: () => get<DeviceStatus[]>('/latest_status'),
  equipment: (id?: string) =>
    get<Equipment[]>('/equipment' + (id ? `?equipment_id=${id}` : '')),
  metric: (equipmentId: string, hours = 24) =>
    get<Metric[]>(`/metric/${equipmentId}?hours=${hours}`),
  historyFaults: (equipmentId: string) =>
    get<HistoryFault[]>(`/history_faults/${equipmentId}`),
  workOrders: (status?: string, equipmentId?: string) => {
    const qs = new URLSearchParams();
    if (status) qs.set('status', status);
    if (equipmentId) qs.set('equipment_id', equipmentId);
    const q = qs.toString();
    return get<WorkOrder[]>('/work_orders' + (q ? `?${q}` : ''));
  },
  knowledge: (q?: string) =>
    get<Knowledge[]>('/knowledge' + (q ? `?q=${encodeURIComponent(q)}` : '')),
  closeWorkOrder: (woId: string) =>
    post<WorkOrder>(`/work_orders/${woId}/close`),
  assignWorkOrder: (woId: string, operator: string) =>
    post<WorkOrder>(`/work_orders/${woId}/assign`, { operator }),
  doneWorkOrder: (woId: string, resolution: string, operator?: string) =>
    post<WorkOrder>(`/work_orders/${woId}/done`, { resolution, operator }),
  workOrderLogs: (woId: string) =>
    get<WorkOrderLog[]>(`/work_orders/${woId}/logs`),
  createKnowledge: (input: {
    fault_code: string;
    symptom: string;
    root_cause: string;
    resolution: string;
    source_wo_id?: string | null;
  }) => post<{ id: string; created_at: string }>('/knowledge/create', input),
  inspect: (equipmentId: string) =>
    post<{
      equipment_id: string;
      over_threshold: string[];
      new_work_order: boolean;
      open_work_orders: number;
    }>(`/inspect/${equipmentId}`),
  /** 班次简报：不传 at 即「当班至今」；传班次边界（如 2026-11-07 20:00）取刚结束那一班 */
  shiftReport: (at?: string) => {
    const qs = new URLSearchParams();
    if (at) {
      qs.set('at', at);
      qs.set('ended', '1');
    }
    const q = qs.toString();
    return get<ShiftReport>('/shift_report' + (q ? `?${q}` : ''));
  },
  sendShiftReport: (at?: string) =>
    post<{ ok: boolean; note: string; report: ShiftReport }>(
      '/shift_report/send',
      at ? { at, ended: true } : {},
    ),
};
