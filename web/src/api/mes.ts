/**
 * mes.ts — 对接自研 mcp-server-mes 的 HTTP API（经 vite /mes-api 代理 → api.py:8002）
 * 与 HappyClaw 自己的 /api（后端 3000）隔离，避免冲突。
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

const base = '/mes-api';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(base + path);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<T>;
}

export const mesApi = {
  deviceStatus: () => get<DeviceStatus[]>('/latest_status'),
  equipment: (id?: string) =>
    get<Equipment[]>('/equipment' + (id ? `?equipment_id=${id}` : '')),
  metric: (equipmentId: string, hours = 24) =>
    get<Metric[]>(`/metric/${equipmentId}?hours=${hours}`),
  workOrders: (status?: string) =>
    get<WorkOrder[]>('/work_orders' + (status ? `?status=${status}` : '')),
  knowledge: () => get<Knowledge[]>('/knowledge'),
  closeWorkOrder: (woId: string) =>
    get<WorkOrder>(`/work_orders/${woId}/close`),
};
