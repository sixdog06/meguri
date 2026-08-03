<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import SeichiMap from './components/SeichiMap.vue'
import type { ChatMessage, Itinerary, ItineraryDay, SeichiCandidate, TransitLeg } from './types'
import { dayColor } from './types'

const STORAGE_KEY = 'meguri_conversation_id'

const conversationId = ref<string | null>(null)
const messages = ref<ChatMessage[]>([])
const seichi = ref<SeichiCandidate[]>([]) // 最近一轮检索出的候选圣地，用于地图标点
const itinerary = ref<Itinerary | null>(null) // 最近生成的行程快照（按天视图 + 每日路线）
const input = ref('')
const sending = ref(false)
const progress = ref<string | null>(null)
const error = ref<string | null>(null)

const progressLabels: Record<string, string> = {
  received: '已收到消息',
  thinking: '正在思考…',
}

const legModeLabels: Record<string, string> = {
  walk: '步行',
  drive: '车程',
}

function legLabel(leg: TransitLeg): string {
  const mode = legModeLabels[leg.mode] ?? leg.mode
  return `${mode} ${leg.duration_minutes} 分钟 · ${leg.distance_km} km`
}

/** 天内段按圣地 id 对齐（leg.from_id/to_id 引用 seichi.id，重名不断链）。 */
function legBetween(day: ItineraryDay, i: number): TransitLeg | undefined {
  const from = day.seichi[i]
  const to = day.seichi[i + 1]
  if (!from || !to) return undefined
  return day.legs.find((l) => !l.cross_day && l.from_id === from.id && l.to_id === to.id)
}

/** 跨天连接段：每天末尾到次日开头（挂在出发天 legs 末尾）。 */
function crossDayLeg(day: ItineraryDay): TransitLeg | undefined {
  return day.legs.find((l) => l.cross_day)
}

let eventSource: EventSource | null = null

function subscribeEvents(id: string) {
  eventSource?.close()
  eventSource = new EventSource(`/api/conversations/${id}/events`)
  eventSource.onmessage = (e) => {
    const item = JSON.parse(e.data as string) as { event: string; data?: { stage?: string } }
    if (item.event === 'done') {
      progress.value = null
    } else if (item.event === 'planning') {
      // 规划各阶段进度（检索中/聚类中/排序中/完成）
      progress.value = item.data?.stage ?? '规划中…'
    } else {
      progress.value = progressLabels[item.event] ?? item.event
    }
  }
}

async function ensureConversation(): Promise<string> {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved) {
    const res = await fetch(`/api/conversations/${saved}/messages`)
    if (res.ok) {
      // 会话仍在：载入历史（刷新页面不丢消息），并恢复最近一轮的地图标点与行程快照
      messages.value = (await res.json()) as ChatMessage[]
      const withSeichi = [...messages.value].reverse().find((m) => m.payload?.search_seichi?.length)
      seichi.value = withSeichi?.payload?.search_seichi ?? []
      const itineraryRes = await fetch(`/api/conversations/${saved}/itinerary`)
      if (itineraryRes.ok) {
        itinerary.value = ((await itineraryRes.json()) as { itinerary: Itinerary | null }).itinerary
      }
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
    const body = (await res.json()) as {
      reply: string
      seichi?: SeichiCandidate[]
      itinerary?: Itinerary | null
    }
    const candidates = body.seichi ?? []
    const payload: ChatMessage['payload'] = {}
    if (candidates.length) payload.search_seichi = candidates
    if (body.itinerary) payload.plan_itinerary = body.itinerary
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: body.reply,
      payload: Object.keys(payload).length ? payload : null,
    })
    // 总是替换：新一轮检索为空时也要清掉旧标点，与“无结果”的回复一致
    seichi.value = candidates
    itinerary.value = body.itinerary ?? null
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
      <section v-if="itinerary" class="itinerary">
        <h2>{{ itinerary.work ?? '行程' }} · {{ itinerary.day_count }} 天</h2>
        <div v-for="day in itinerary.days" :key="day.day" class="day">
          <h3>
            <span class="day-dot" :style="{ background: dayColor(day.day) }" />
            Day {{ day.day }}
          </h3>
          <ol class="day-stops">
            <template v-for="(s, i) in day.seichi" :key="s.id ?? s.name">
              <li class="stop">{{ s.name }}</li>
              <li v-if="legBetween(day, i)" class="leg">
                ↓ {{ legLabel(legBetween(day, i)!) }}
                <span v-if="legBetween(day, i)!.estimate" class="estimate">估算</span>
              </li>
            </template>
          </ol>
          <p v-if="crossDayLeg(day)" class="leg cross-day">
            → 次日：{{ legLabel(crossDayLeg(day)!) }}
            <span v-if="crossDayLeg(day)!.estimate" class="estimate">估算</span>
          </p>
        </div>
      </section>
      <form class="composer" @submit.prevent="send">
        <input v-model="input" :disabled="sending" placeholder="想去哪里巡礼？" />
        <button type="submit" :disabled="sending || !input.trim()">发送</button>
      </form>
    </section>
    <section class="map-panel">
      <SeichiMap :seichi="seichi" :itinerary="itinerary" />
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
.itinerary h2 {
  font-size: 1rem;
  margin: 0.5rem 0 0.25rem;
}
.itinerary h3 {
  font-size: 0.95rem;
  margin: 0.5rem 0 0.25rem;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.day-dot {
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 50%;
  display: inline-block;
}
.day-stops {
  margin: 0 0 0 1.25rem;
  padding: 0;
}
.day-stops .stop {
  margin: 0.15rem 0;
}
.day-stops .leg {
  list-style: none;
  color: #6b7280;
  font-size: 0.85rem;
  margin-left: -1rem;
}
.cross-day {
  color: #6b7280;
  font-size: 0.85rem;
  margin: 0.25rem 0 0;
}
.estimate {
  background: #fef3c7;
  color: #92400e;
  border-radius: 4px;
  padding: 0 0.3rem;
  font-size: 0.75rem;
  margin-left: 0.25rem;
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
