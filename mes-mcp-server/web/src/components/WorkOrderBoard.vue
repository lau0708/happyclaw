<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { WorkOrder } from '../types'

const orders = ref<WorkOrder[]>([])
const statusFilter = ref('')
const loading = ref(false)
const closing = ref('')

const statusLabel: Record<string, string> = {
  open: '待处理',
  assigned: '已分派',
  in_progress: '处理中',
  done: '已完成',
  closed: '已闭环'
}

async function load() {
  loading.value = true
  try {
    orders.value = await api.workOrders(statusFilter.value || undefined)
  } finally {
    loading.value = false
  }
}

async function closeOrder(woId: string) {
  closing.value = woId
  try {
    await api.closeWorkOrder(woId)
    await load()
  } finally {
    closing.value = ''
  }
}

onMounted(load)
</script>

<template>
  <section class="panel">
    <div class="head">
      <h2 class="title">工单列表</h2>
      <div class="filters">
        <select v-model="statusFilter" @change="load">
          <option value="">全部状态</option>
          <option v-for="(label, key) in statusLabel" :key="key" :value="key">{{ label }}</option>
        </select>
        <button @click="load" class="btn" :disabled="loading">{{ loading ? '加载中…' : '刷新' }}</button>
      </div>
    </div>
    <table class="table" v-if="orders.length">
      <thead>
        <tr>
          <th>工单号</th><th>设备</th><th>故障描述</th><th>级别</th><th>状态</th><th>创建时间</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="o in orders" :key="o.wo_id">
          <td class="mono">{{ o.wo_id }}</td>
          <td>{{ o.equipment_id }}</td>
          <td class="desc">{{ o.fault_desc }}</td>
          <td><span class="sev" :class="o.severity">{{ o.severity }}</span></td>
          <td><span class="st" :class="o.status">{{ statusLabel[o.status] || o.status }}</span></td>
          <td class="mono">{{ o.created_at }}</td>
          <td>
            <button
              v-if="o.status !== 'closed'"
              class="btn small"
              @click="closeOrder(o.wo_id)"
              :disabled="closing === o.wo_id"
            >闭环</button>
            <span v-else class="done">✔</span>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty">{{ loading ? '加载中…' : '暂无工单' }}</p>
  </section>
</template>

<style scoped>
.panel { background: #fff; border-radius: 12px; padding: 18px 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.title { font-size: 15px; margin: 0; }
.filters { display: flex; gap: 8px; }
.btn { border: 1px solid #cbd5e1; background: #fff; border-radius: 8px; padding: 5px 12px; cursor: pointer; font-size: 13px; }
.btn.small { padding: 3px 10px; }
.btn:disabled { opacity: .5; cursor: default; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #f1f5f9; }
th { color: #64748b; font-weight: 500; }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
.desc { max-width: 320px; }
.sev { padding: 2px 8px; border-radius: 999px; font-size: 12px; background: #f1f5f9; }
.sev.high, .sev.critical { background: #fee2e2; color: #b91c1c; }
.st { padding: 2px 8px; border-radius: 999px; font-size: 12px; }
.st.open, .st.assigned { background: #fef3c7; color: #92400e; }
.st.in_progress { background: #dbeafe; color: #1d4ed8; }
.st.done, .st.closed { background: #dcfce7; color: #166534; }
.done { color: #16a34a; }
.empty { color: #94a3b8; font-size: 13px; }
</style>
