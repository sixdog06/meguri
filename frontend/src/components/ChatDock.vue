<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import type { ChatMessage } from '../types'

/** assistant 回复是 Markdown：解析 + 消毒后渲染（LLM 输出不可信，必须过 DOMPurify）。 */
function md(text: string): string {
  return DOMPurify.sanitize(marked.parse(text, { async: false }))
}

const props = defineProps<{
  messages: ChatMessage[]
  progress: string | null // 规划各阶段进度（检索中/聚类中/排序中…），null = 空闲
  sending: boolean
  streaming: string // 正在逐字流入的 assistant 回复（真流式），空串 = 无
}>()

const emit = defineEmits<{ send: [text: string]; dragstart: [e: PointerEvent] }>()

const open = ref(true)
const input = ref('')
const listEl = ref<HTMLUListElement | null>(null)

const lastMessage = ref<ChatMessage | null>(null)
watch(
  () => props.messages.length,
  async () => {
    lastMessage.value = props.messages[props.messages.length - 1] ?? null
    // 新消息滚动到底部
    await nextTick()
    listEl.value?.scrollTo({ top: listEl.value.scrollHeight })
  },
  { immediate: true },
)
// 流式文本每进一段都跟滚到底部
watch(
  () => props.streaming,
  async () => {
    await nextTick()
    listEl.value?.scrollTo({ top: listEl.value.scrollHeight })
  },
)

function submit() {
  const text = input.value.trim()
  if (!text || props.sending) return
  input.value = ''
  open.value = true
  emit('send', text)
}

/** 标题栏按下：交给 App 拖动；松开时几乎没动 = 单击，折叠/展开消息区。 */
function onBarPointerDown(e: PointerEvent) {
  const sx = e.clientX
  const sy = e.clientY
  const onUp = (ev: PointerEvent) => {
    if (Math.hypot(ev.clientX - sx, ev.clientY - sy) < 5) open.value = !open.value
    window.removeEventListener('pointerup', onUp)
  }
  window.addEventListener('pointerup', onUp)
  emit('dragstart', e)
}
</script>

<template>
  <section class="dock">
    <div class="dock-bar" @pointerdown="onBarPointerDown">
      <span class="grip">⠿</span>
      <span class="bar-title">对话 {{ open ? '▾' : '▴' }}</span>
      <span v-if="!open && lastMessage" class="dock-preview">{{ lastMessage.content }}</span>
    </div>
    <ul v-if="open" ref="listEl" class="messages">
      <li v-for="m in messages" :key="m.id" :class="m.role">
        <span v-if="m.role === 'assistant'" class="bubble markdown" v-html="md(m.content)"></span>
        <span v-else class="bubble">{{ m.content }}</span>
      </li>
      <li v-if="streaming" class="assistant">
        <span class="bubble markdown streaming"><span v-html="md(streaming)"></span><span class="cursor">▍</span></span>
      </li>
      <li v-else-if="progress" class="assistant">
        <span class="bubble muted">{{ progress }}</span>
      </li>
    </ul>
    <form class="composer" @submit.prevent="submit">
      <input v-model="input" :disabled="sending" placeholder="想去哪里巡礼？" />
      <button type="submit" :disabled="sending || !input.trim()">发送</button>
    </form>
  </section>
</template>

<style scoped>
.dock {
  height: 100%;
  box-sizing: border-box;
  background: rgb(250 249 245 / 0.96);
  backdrop-filter: blur(8px);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: var(--shadow);
  padding: 0.3rem 0.9rem 0.65rem;
  display: flex;
  flex-direction: column;
}
.dock-bar {
  display: flex;
  gap: 0.6rem;
  align-items: baseline;
  padding: 0.3rem 0;
  font-size: 0.8rem;
  letter-spacing: 0.08em;
  color: var(--ink-faint);
  cursor: move;
  user-select: none;
  touch-action: none;
  transition: color 0.15s;
}
.dock-bar:hover {
  color: var(--ink);
}
.grip {
  font-size: 0.75rem;
  letter-spacing: 0;
}
.bar-title {
  flex-shrink: 0;
}
.dock-preview {
  letter-spacing: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.messages {
  list-style: none;
  margin: 0.35rem 0;
  padding: 0;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.messages li {
  display: flex;
}
.messages .user {
  justify-content: flex-end;
}
.bubble {
  max-width: 85%;
  border-radius: 10px;
  padding: 0.4rem 0.75rem;
  font-size: 0.9rem;
  line-height: 1.6;
  background: var(--paper-deep);
  white-space: pre-wrap;
}
.user .bubble {
  background: var(--ink);
  color: var(--paper);
}
.bubble.markdown {
  white-space: normal; /* Markdown 渲染后按块级布局，不再保留纯文本换行语义 */
}
.bubble.markdown :deep(> *:first-child) {
  margin-top: 0;
}
.bubble.markdown :deep(> *:last-child) {
  margin-bottom: 0;
}
.bubble.markdown :deep(h1),
.bubble.markdown :deep(h2),
.bubble.markdown :deep(h3),
.bubble.markdown :deep(h4) {
  margin: 0.7em 0 0.35em;
  font-size: 1em;
  line-height: 1.4;
}
.bubble.markdown :deep(p) {
  margin: 0.4em 0;
}
.bubble.markdown :deep(ul),
.bubble.markdown :deep(ol) {
  margin: 0.35em 0;
  padding-left: 1.3em;
}
.bubble.markdown :deep(li) {
  margin: 0.15em 0;
}
.bubble.markdown :deep(code) {
  background: rgb(0 0 0 / 0.06);
  border-radius: 4px;
  padding: 0.1em 0.3em;
  font-size: 0.85em;
}
.bubble.markdown :deep(pre) {
  background: rgb(0 0 0 / 0.06);
  border-radius: 8px;
  padding: 0.5em 0.7em;
  overflow-x: auto;
}
.bubble.markdown :deep(pre code) {
  background: none;
  padding: 0;
}
.bubble.markdown :deep(blockquote) {
  margin: 0.4em 0;
  padding-left: 0.7em;
  border-left: 3px solid var(--line);
  color: var(--ink-faint);
}
.bubble.markdown :deep(table) {
  border-collapse: collapse;
  margin: 0.4em 0;
  font-size: 0.85em;
}
.bubble.markdown :deep(th),
.bubble.markdown :deep(td) {
  border: 1px solid var(--line);
  padding: 0.2em 0.5em;
}
.bubble.markdown :deep(hr) {
  border: none;
  border-top: 1px solid var(--line);
  margin: 0.6em 0;
}
.bubble.markdown :deep(a) {
  color: inherit;
}
.bubble.muted {
  background: transparent;
  color: var(--ink-faint);
  font-style: italic;
  padding-left: 0;
}
.bubble.streaming .cursor {
  color: var(--ink-faint);
  animation: blink 1s steps(1) infinite;
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}
.composer {
  display: flex;
  gap: 0.5rem;
}
.composer input {
  flex: 1;
  padding: 0.55rem 0.75rem;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fffdf9;
  color: var(--ink);
  font-size: 0.9rem;
  min-width: 0;
  outline: none;
  transition: border-color 0.15s;
}
.composer input:focus {
  border-color: var(--ink-faint);
}
.composer input::placeholder {
  color: var(--ink-faint);
}
.composer button {
  padding: 0.55rem 1.1rem;
  border: none;
  border-radius: 8px;
  background: var(--ink);
  color: var(--paper);
  font-size: 0.9rem;
  cursor: pointer;
  transition: background 0.15s;
}
.composer button:hover:not(:disabled) {
  background: #403e39;
}
.composer button:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
