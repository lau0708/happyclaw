<script setup lang="ts">
import { onMounted, ref } from 'vue'
import EquipmentOverview from './components/EquipmentOverview.vue'
import WorkOrderBoard from './components/WorkOrderBoard.vue'
import KnowledgePanel from './components/KnowledgePanel.vue'
import { api } from './api'
import type { DeviceStatus } from './types'

const devices = ref<DeviceStatus[]>([])
const error = ref('')

onMounted(async () => {
  try {
    devices.value = await api.deviceStatus()
  } catch (e) {
    error.value = String(e)
  }
})
</script>

<template>
  <div class="app">
    <header class="topbar">
      <h1>设备异常工单看板</h1>
      <span class="sub">HappyClaw · mcp-server-mes 模拟 MES</span>
    </header>
    <p v-if="error" class="err">后端未连接：请先运行 <code>python3 api.py</code>（端口 8002）</p>
    <EquipmentOverview :devices="devices" />
    <WorkOrderBoard />
    <KnowledgePanel />
  </div>
</template>
