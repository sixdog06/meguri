<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import SeichiMap from './components/SeichiMap.vue'
import type { ChatMessage, SeichiCandidate } from './types'

const STORAGE_KEY = 'meguri_conversation_id'

const conversationId = ref<string | null>(null)
const messages = ref<ChatMessage[]>([])
const seichi = ref<SeichiCandidate[]>([]) // 最近一轮检索出的候选圣地，用于地图标点
const input = ref('')
const sending = ref(false)
const progress = ref<string | null>(null)
const error = ref<string | null>(null)

const progressLabels: Record<string, string> = {
  received: '已收到消息',
  thinking: '正在思考…',
}

let eventSource: EventSource | null = null

function subscribeEvents(id: string) {
  eventSource?.close()
  eventSource = new EventSource(`/api/conversations/${id}/events`)
  eventSource.onmessage = (e) => {
    const item = JSON.parse(e.data as string) as { event: string }
    progress.value = item.event === 'done' ? null : (progressLabels[item.event] ?? item.event)
  }
}

async function ensureConversation(): Promise<string> {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved) {
    const res = await fetch(`/api/conversations/${saved}/messages`)
    if (res.ok) {
      // 会话仍在：载入历史（刷新页面不丢消息），并恢复最近一轮的地图标点
      messages.value = (await res.json()) as ChatMessage[]
      const withSeichi = [...messages.value].reverse().find((m) => m.payload?.search_seichi?.length)
      seichi.value = withSeichi?.payload?.search_seichi ?? []
      return saved
    }
    localStorage.removeItem(STORAGE_KEY)
  }
  const res = await fetch('/api/conversations', { method: 'POST' })
  if (!res.ok) throw new Error(`创建会话失败：HTTP ${res.status}`)
  const body = (await res.json()) as { conversation_id: string }
  localStorage.setItem(STORAGE_KEY, body.conversation_id)
  return body.conversation_id
}

async function send() {
  const text = input.value.trim()
  if (!text || !conversationId.value || sending.value) return
  sending.value = true
  error.value = null
  messages.value.push({ id: Date.now(), role: 'user', content: text, payload: null })
  input.value = ''
  try {
    const res = await fetch(`/api/conversations/${conversationId.value}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const body = (await res.json()) as { reply: string; seichi?: SeichiCandidate[] }
    const candidates = body.seichi ?? []
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: body.reply,
      payload: candidates.length ? { search_seichi: candidates } : null,
    })
    // 总是替换：新一轮检索为空时也要清掉旧标点，与“无结果”的回复一致
    seichi.value = candidates
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    sending.value = false
    progress.value = null
  }
}

onMounted(async () => {
  try {
    conversationId.value = await ensureConversation()
    subscribeEvents(conversationId.value)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
})

onBeforeUnmount(() => eventSource?.close())
</script>

<template>
  <main class="layout">
    <section class="chat">
      <h1>Meguri 圣地巡礼</h1>
      <p v-if="error" class="error">出错了：{{ error }}</p>
      <ul class="messages">
        <li v-for="m in messages" :key="m.id" :class="m.role">
          <span class="role">{{ m.role === 'user' ? '我' : 'Meguri' }}</span>
          <span class="content">{{ m.content }}</span>
        </li>
      </ul>
      <p v-if="progress" class="progress">{{ progress }}</p>
      <form class="composer" @submit.prevent="send">
        <input v-model="input" :disabled="sending" placeholder="想去哪里巡礼？" />
        <button type="submit" :disabled="sending || !input.trim()">发送</button>
      </form>
    </section>
    <section class="map-panel">
      <SeichiMap :seichi="seichi" />
    </section>
  </main>
</template>

<style scoped>
.layout {
  display: flex;
  gap: 1rem;
  padding: 1rem;
  align-items: stretch;
}
.chat {
  flex: 1;
  min-width: 0;
  max-width: 640px;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.map-panel {
  flex: 1;
  min-width: 0;
}
@media (max-width: 800px) {
  .layout {
    flex-direction: column;
  }
  .chat {
    max-width: none;
  }
}
.messages {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.messages li {
  display: flex;
  gap: 0.5rem;
}
.role {
  font-weight: bold;
  flex-shrink: 0;
}
.messages .user .role {
  color: #2563eb;
}
.messages .assistant .role {
  color: #059669;
}
.progress {
  color: #6b7280;
  font-style: italic;
}
.error {
  color: #dc2626;
}
.composer {
  display: flex;
  gap: 0.5rem;
}
.composer input {
  flex: 1;
  padding: 0.5rem;
}
.composer button {
  padding: 0.5rem 1rem;
}
</style>
