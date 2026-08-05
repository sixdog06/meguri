<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ChatDock from './components/ChatDock.vue'
import ItineraryPanel from './components/ItineraryPanel.vue'
import SeichiMap from './components/SeichiMap.vue'
import type { ChatMessage, Itinerary, SeichiCandidate } from './types'

const STORAGE_KEY = 'meguri_conversation_id'

const conversationId = ref<string | null>(null)
const messages = ref<ChatMessage[]>([])
const seichi = ref<SeichiCandidate[]>([]) // 最近一轮检索出的候选圣地，用于地图标点
const itinerary = ref<Itinerary | null>(null) // 最近生成的行程快照（时间轴面板 + 每日路线）
const candidates = ref<SeichiCandidate[]>([]) // “添加圣地”候选（排除已在行程内的）
const editing = ref(false) // 编辑请求进行中
const sending = ref(false)
const progress = ref<string | null>(null)
const streamingReply = ref('') // 正在逐字流入的 assistant 回复（SSE reply_chunk），上屏即清
const error = ref<string | null>(null)
const notice = ref<string | null>(null) // 显式业务提示（如"该作品没有圣地巡礼数据"，区别于故障与加载中）

// 对话窗：可拖动（标题栏）/可缩放（右下角原生手柄）的浮窗；行程面板贴其右缘，可折叠成竖条
const dockEl = ref<HTMLElement | null>(null)
const dockX = ref(24)
const dockY = ref(120)
const dockW = ref(380) // 初值，实际尺寸由 ResizeObserver 跟踪（用户可拖 resize 手柄改变）
const dockH = ref(340)
const itinCollapsed = ref(false)

function onDockDragStart(e: PointerEvent) {
  const startX = e.clientX
  const startY = e.clientY
  const originX = dockX.value
  const originY = dockY.value
  const onMove = (ev: PointerEvent) => {
    dockX.value = Math.min(Math.max(0, originX + ev.clientX - startX), window.innerWidth - dockW.value)
    dockY.value = Math.min(Math.max(0, originY + ev.clientY - startY), window.innerHeight - dockH.value)
  }
  const onUp = () => {
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}

/** 行程面板位置：贴对话窗右缘；右侧放不下时翻到左缘；顶部随对话窗但不出视口。 */
const itinStyle = computed(() => {
  const gap = 12
  const width = Math.min(400, window.innerWidth - 24)
  let left = dockX.value + dockW.value + gap
  if (left + width > window.innerWidth - 12) {
    left = Math.max(12, dockX.value - width - gap)
  }
  const top = Math.min(dockY.value, Math.max(12, window.innerHeight - 420))
  return { left: `${left}px`, top: `${top}px`, width: `${width}px` }
})

const progressLabels: Record<string, string> = {
  received: '已收到消息',
  thinking: '正在思考…',
}

/** 编辑（#9）：拉取“添加圣地”候选（排除已在行程内的）。 */
async function refreshCandidates() {
  if (!conversationId.value || !itinerary.value) {
    candidates.value = []
    return
  }
  const res = await fetch(`/api/conversations/${conversationId.value}/itinerary/candidates`)
  if (res.ok) {
    candidates.value = ((await res.json()) as { candidates: SeichiCandidate[] }).candidates
  }
}

/** 编辑（#9）：应用一次操作，后端自动重跑校验/讲解，返回新快照整页刷新。 */
async function postEdit(body: Record<string, unknown>) {
  if (!conversationId.value || editing.value) return
  editing.value = true
  error.value = null
  progress.value = '正在重新校验行程…' // live 模式下重跑交通/开放时间校验，可能数十秒——给出明确的进行中反馈，不是卡死
  try {
    const res = await fetch(`/api/conversations/${conversationId.value}/itinerary/edits`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      // 422 等错误带后端 detail（如"圣地已在行程中"），展示具体原因
      const resBody = (await res.json().catch(() => null)) as { detail?: string } | null
      throw new Error(resBody?.detail ?? `编辑失败：HTTP ${res.status}`)
    }
    itinerary.value = ((await res.json()) as { itinerary: Itinerary }).itinerary
    await refreshCandidates()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    editing.value = false
    progress.value = null
  }
}

let eventSource: EventSource | null = null

function subscribeEvents(id: string) {
  eventSource?.close()
  eventSource = new EventSource(`/api/conversations/${id}/events`)
  eventSource.onmessage = (e) => {
    const item = JSON.parse(e.data as string) as { event: string; data?: { stage?: string; text?: string } }
    if (item.event === 'done') {
      progress.value = null
    } else if (item.event === 'reply_chunk') {
      // 真流式：模型逐字输出，即时上屏（进度提示同时让位）
      progress.value = null
      streamingReply.value += item.data?.text ?? ''
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
        await refreshCandidates()
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

async function send(text: string) {
  if (!text || !conversationId.value || sending.value) return
  sending.value = true
  error.value = null
  notice.value = null
  streamingReply.value = '' // 新一轮：清掉上一轮的流式残影
  messages.value.push({ id: Date.now(), role: 'user', content: text, payload: null })
  try {
    const res = await fetch(`/api/conversations/${conversationId.value}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (!res.ok) {
      // 503/网络错误带后端 detail（如"模型服务暂时不可用"），展示具体原因
      const resBody = (await res.json().catch(() => null)) as { detail?: string } | null
      throw new Error(resBody?.detail ?? `请求失败：HTTP ${res.status}`)
    }
    const body = (await res.json()) as {
      reply: string
      seichi?: SeichiCandidate[]
      itinerary?: Itinerary | null
      notice?: string | null
    }
    const found = body.seichi ?? []
    notice.value = body.notice ?? null  // 显式业务提示（如"该作品没有圣地巡礼数据"）
    const payload: ChatMessage['payload'] = {}
    if (found.length) payload.search_seichi = found
    if (body.itinerary) payload.plan_itinerary = body.itinerary
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: body.reply,
      payload: Object.keys(payload).length ? payload : null,
    })
    streamingReply.value = '' // 正式消息入列，流式气泡让位（避免重复显示）
    // 总是替换：新一轮检索为空时也要清掉旧标点，与“无结果”的回复一致
    seichi.value = found
    itinerary.value = body.itinerary ?? null
    if (itinerary.value) await refreshCandidates()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    streamingReply.value = '' // 出错不留半截流式文本
  } finally {
    sending.value = false
    progress.value = null
  }
}

let resizeObserver: ResizeObserver | null = null

function clampDock() {
  dockX.value = Math.min(Math.max(0, dockX.value), window.innerWidth - dockW.value)
  dockY.value = Math.min(Math.max(0, dockY.value), window.innerHeight - dockH.value)
}

onMounted(async () => {
  dockY.value = Math.max(80, Math.round((window.innerHeight - dockH.value) / 2))
  if (dockEl.value) {
    // 用户拖 resize 手柄改变尺寸后，行程面板位置需要跟着走
    resizeObserver = new ResizeObserver(() => {
      if (!dockEl.value) return
      dockW.value = dockEl.value.offsetWidth
      dockH.value = dockEl.value.offsetHeight
    })
    resizeObserver.observe(dockEl.value)
  }
  window.addEventListener('resize', clampDock)
  try {
    conversationId.value = await ensureConversation()
    subscribeEvents(conversationId.value)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
})

onBeforeUnmount(() => {
  eventSource?.close()
  resizeObserver?.disconnect()
  window.removeEventListener('resize', clampDock)
})
</script>

<template>
  <div class="app">
    <SeichiMap class="bg-map" :seichi="seichi" :itinerary="itinerary" />

    <header class="title-card">
      <span class="seal" />
      <div>
        <h1>Meguri 圣地巡礼</h1>
        <p v-if="itinerary?.work">{{ itinerary.work }}<template v-if="itinerary.area"> · {{ itinerary.area }}</template></p>
      </div>
    </header>

    <p v-if="error" class="toast-error">出错了：{{ error }}</p>
    <p v-if="notice" class="toast-notice">提示：{{ notice }}</p>

    <div ref="dockEl" class="dock-win" :style="{ left: dockX + 'px', top: dockY + 'px' }">
      <ChatDock :messages="messages" :progress="progress" :sending="sending" :streaming="streamingReply" @send="send" @dragstart="onDockDragStart" />
    </div>

    <template v-if="itinerary">
      <button
        v-if="itinCollapsed"
        class="itin-strip"
        :style="{ left: itinStyle.left, top: itinStyle.top }"
        title="展开行程"
        @click="itinCollapsed = false"
      >
        行程 ▸
      </button>
      <div v-else class="panel-win" :style="itinStyle">
        <ItineraryPanel
          :itinerary="itinerary"
          :candidates="candidates"
          :editing="editing"
          @edit="postEdit"
          @collapse="itinCollapsed = true"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.app {
  position: fixed;
  inset: 0;
}
.bg-map {
  position: absolute;
  inset: 0;
}
.bg-map :deep(.map) {
  height: 100%;
  min-height: 0;
  border-radius: 0;
}
.title-card {
  position: absolute;
  top: 1.25rem;
  right: 1.25rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  background: rgb(250 249 245 / 0.94);
  backdrop-filter: blur(8px);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0.55rem 0.95rem;
  box-shadow: var(--shadow);
  z-index: 500;
}
.seal {
  width: 0.85rem;
  height: 0.85rem;
  background: var(--accent);
  border-radius: 2px;
  flex-shrink: 0;
}
.title-card h1 {
  font-family: var(--font-serif);
  font-size: 0.98rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  margin: 0;
}
.title-card p {
  margin: 0.15rem 0 0;
  font-size: 0.78rem;
  color: var(--ink-faint);
}
.toast-error,
.toast-notice {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  border-radius: 8px;
  padding: 0.4rem 0.9rem;
  margin: 0;
  font-size: 0.88rem;
  box-shadow: var(--shadow);
  z-index: 600;
}
.toast-error {
  top: 1.25rem;
  background: var(--accent-soft-bg);
  color: var(--accent);
  border: 1px solid #e8c7bd;
}
.toast-notice {
  top: 3.4rem;
  background: var(--amber-bg);
  color: var(--amber-ink);
  border: 1px solid #e3d5ae;
}
.dock-win {
  position: absolute;
  z-index: 500;
  width: min(380px, calc(100vw - 32px));
  min-width: 280px;
  min-height: 120px;
  max-width: calc(100vw - 24px);
  max-height: calc(100vh - 24px);
  resize: both;
  overflow: hidden;
  display: flex;
}
.dock-win > * {
  flex: 1;
  min-height: 0;
}
.panel-win {
  position: absolute;
  z-index: 500;
  display: flex;
  max-height: calc(100vh - 24px);
}
.panel-win > * {
  flex: 1;
  min-height: 0;
}
.itin-strip {
  position: absolute;
  z-index: 500;
  writing-mode: vertical-rl;
  background: rgb(250 249 245 / 0.96);
  backdrop-filter: blur(8px);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0.8rem 0.45rem;
  font-size: 0.82rem;
  letter-spacing: 0.25em;
  color: var(--ink-soft);
  cursor: pointer;
  box-shadow: var(--shadow);
  transition: color 0.15s;
}
.itin-strip:hover {
  color: var(--ink);
}
@media (max-width: 800px) {
  .title-card {
    display: none;
  }
}
</style>
