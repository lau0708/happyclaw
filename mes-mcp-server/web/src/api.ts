import type { WorkOrder, Knowledge, DeviceStatus } from './types'

const base = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(base + path)
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<T>
}

export const api = {
  deviceStatus: () => get<DeviceStatus[]>('/latest_status'),
  workOrders: (status?: string) =>
    get<WorkOrder[]>('/work_orders' + (status ? `?status=${status}` : '')),
  knowledge: () => get<Knowledge[]>('/knowledge'),
  closeWorkOrder: (woId: string) => get<WorkOrder>(`/work_orders/${woId}/close`)
}
