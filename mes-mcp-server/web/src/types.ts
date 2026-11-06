export interface Equipment {
  equipment_id: string
  name: string
  type: string
  line: string
  status: string
}

export interface WorkOrder {
  wo_id: string
  equipment_id: string
  fault_desc: string
  severity: string
  status: string
  assignee: string | null
  plan: string | null
  created_at: string
  updated_at: string
}

export interface Knowledge {
  id: string
  fault_code: string
  symptom: string
  root_cause: string
  resolution: string
  source_wo_id: string | null
  created_at: string
}

export interface DeviceStatus {
  equipment_id: string
  name: string
  status: string
  over_threshold: string[]
  open_work_orders: number
  latest_metric: { vibration?: number; temp?: number; pressure?: number } | null
}
