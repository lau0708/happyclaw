<script setup lang="ts">
import type { DeviceStatus } from '../types'

defineProps<{ devices: DeviceStatus[] }>()

const overLabel: Record<string, string> = {
  vibration: '振动',
  temp: '温度',
  pressure: '压力'
}
</script>

<template>
  <section class="panel">
    <h2 class="title">设备概览</h2>
    <div class="grid" v-if="devices.length">
      <div
        v-for="d in devices"
        :key="d.equipment_id"
        class="card"
        :class="{ alarm: d.over_threshold.length > 0 }"
      >
        <div class="card-head">
          <span class="name">{{ d.name }}</span>
          <span class="eid">{{ d.equipment_id }}</span>
        </div>
        <div class="metrics">
          <span>振动 {{ d.latest_metric?.vibration ?? '-' }}mm/s</span>
          <span>温度 {{ d.latest_metric?.temp ?? '-' }}°C</span>
          <span>压力 {{ d.latest_metric?.pressure ?? '-' }}MPa</span>
        </div>
        <div class="badges">
          <span v-if="d.over_threshold.length" class="badge bad">
            ⚠ {{ d.over_threshold.map((o) => overLabel[o] || o).join(' / ') }} 超限
          </span>
          <span v-if="d.open_work_orders" class="badge warn">未闭环工单 ×{{ d.open_work_orders }}</span>
          <span v-else class="badge ok">正常</span>
        </div>
      </div>
    </div>
    <p v-else class="empty">暂无设备数据</p>
  </section>
</template>

<style scoped>
.panel { background: #fff; border-radius: 12px; padding: 18px 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.title { font-size: 15px; margin: 0 0 14px; color: #0f172a; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; }
.card { border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 14px; }
.card.alarm { border-color: #fca5a5; background: #fff7f7; }
.card-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.name { font-weight: 600; font-size: 14px; }
.eid { font-size: 11px; color: #64748b; }
.metrics { display: flex; gap: 10px; flex-wrap: wrap; font-size: 12px; color: #334155; margin-bottom: 10px; }
.badges { display: flex; gap: 6px; flex-wrap: wrap; }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 999px; }
.bad { background: #fee2e2; color: #b91c1c; }
.warn { background: #fef3c7; color: #92400e; }
.ok { background: #dcfce7; color: #166534; }
.empty { color: #94a3b8; font-size: 13px; }
</style>
