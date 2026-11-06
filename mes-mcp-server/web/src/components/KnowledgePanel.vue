<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { Knowledge } from '../types'

const items = ref<Knowledge[]>([])

onMounted(async () => {
  items.value = await api.knowledge()
})
</script>

<template>
  <section class="panel">
    <h2 class="title">故障处置经验（知识回流）</h2>
    <div v-if="items.length" class="list">
      <div v-for="k in items" :key="k.id" class="item">
        <div class="line1">
          <span class="code">{{ k.fault_code }}</span>
          <span class="sym">{{ k.symptom }}</span>
        </div>
        <div class="line2"><b>根因：</b>{{ k.root_cause }}</div>
        <div class="line3"><b>处置：</b>{{ k.resolution }}</div>
      </div>
    </div>
    <p v-else class="empty">暂无沉淀经验</p>
  </section>
</template>

<style scoped>
.panel { background: #fff; border-radius: 12px; padding: 18px 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.title { font-size: 15px; margin: 0 0 14px; }
.list { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.item { border: 1px solid #e2e8f0; border-left: 3px solid #16a34a; border-radius: 8px; padding: 10px 12px; font-size: 13px; }
.line1 { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.code { background: #dcfce7; color: #166534; padding: 1px 8px; border-radius: 999px; font-size: 12px; }
.sym { color: #0f172a; font-weight: 500; }
.line2, .line3 { color: #334155; line-height: 1.6; }
.empty { color: #94a3b8; font-size: 13px; }
</style>
