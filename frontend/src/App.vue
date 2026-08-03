<script setup lang="ts">
import { onMounted, ref } from 'vue'

interface Health {
  status: string
  services: Record<string, string>
  adapters: string
}

const health = ref<Health | null>(null)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    const res = await fetch('/api/health')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    health.value = await res.json()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
})
</script>

<template>
  <main>
    <h1>Meguri 圣地巡礼</h1>
    <p v-if="error">后端连接失败：{{ error }}</p>
    <div v-else-if="health">
      <p>状态：{{ health.status }}</p>
      <ul>
        <li v-for="(v, k) in health.services" :key="k">{{ k }}: {{ v }}</li>
      </ul>
      <p>适配器模式：{{ health.adapters }}</p>
    </div>
    <p v-else>加载中…</p>
  </main>
</template>
