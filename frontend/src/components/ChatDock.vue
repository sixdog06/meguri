<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import type { ChatMessage, ConversationMeta } from '../types'

/** assistant 回复是 Markdown：解析 + 消毒后渲染（LLM 输出不可信，必须过 DOMPurify）。 */
function md(text: string): string {
  return DOMPurify.sanitize(marked.parse(text, { async: false }))
}

const props = defineProps<{
  messages: ChatMessage[]
  progress: string | null // 规划各阶段进度（检索中/聚类中/排序中…），null = 空闲
  sending: boolean
  streaming: string // 正在逐字流入的 assistant 回复（真流式），空串 = 无
  history: ConversationMeta[] // 本地历史（localStorage），倒序
  currentId: string | null // 当前会话 id，历史列表里高亮/禁点
  busy: boolean // 有请求在飞（发送中/编辑校验中）：禁止开新主题与切换
}>()

const emit = defineEmits<{
  send: [text: string]
  dragstart: [e: PointerEvent]
  newtopic: []
  switch: [id: string]
  remove: [id: string]
}>()

const input = ref('')
const listEl = ref<HTMLUListElement | null>(null)
const historyOpen = ref(false) // 历史下拉显隐

watch(
  () => props.messages.length,
  async () => {
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
  emit('send', text)
}

/** 标题栏按下：交给 App 拖动。 */
function onBarPointerDown(e: PointerEvent) {
  emit('dragstart', e)
}

/** 历史条目日期：同年只显示 月/日 时:分，跨年补上年份。 */
function fmtDate(ts: number): string {
  const d = new Date(ts)
  const sameYear = d.getFullYear() === new Date().getFullYear()
  const date = sameYear ? `${d.getMonth() + 1}/${d.getDate()}` : `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
  return `${date} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function onNewTopic() {
  historyOpen.value = false
  emit('newtopic')
}

function onPickTopic(id: string) {
  historyOpen.value = false
  emit('switch', id)
}
</script>

<template>
  <section class="dock">
    <div class="dock-bar" @pointerdown="onBarPointerDown">
      <span class="grip">⠿</span>
      <span class="bar-title">对话</span>
      <!-- 按钮要 stop pointerdown：否则会触发整条标题栏的拖动逻辑 -->
      <span class="bar-actions">
        <button class="bar-btn" :disabled="busy" title="开新主题（当前对话会存入历史）" @pointerdown.stop @click.stop="onNewTopic">新主题</button>
        <button class="bar-btn" title="历史记录" @pointerdown.stop @click.stop="historyOpen = !historyOpen">历史 {{ historyOpen ? '▴' : '▾' }}</button>
      </span>
    </div>
    <div v-if="historyOpen" class="history-pop">
      <p v-if="!history.length" class="history-empty">暂无历史</p>
      <ul v-else>
        <li v-for="c in history" :key="c.id" :class="{ current: c.id === currentId }">
          <button class="history-item" :disabled="busy || c.id === currentId" @click="onPickTopic(c.id)">
            {{ c.title }}
            <span class="history-date">{{ fmtDate(c.updatedAt) }}</span>
          </button>
          <button class="history-del" :disabled="busy" title="删除这条记录（只删本地）" @click="emit('remove', c.id)">✕</button>
        </li>
      </ul>
    </div>
    <ul ref="listEl" class="messages">
      <!-- 空会话的欢迎态：避免一上来就是一块空白 -->
      <li v-if="!messages.length && !streaming" class="welcome">
        <p>想巡礼哪部作品、哪个地方？</p>
        <p class="welcome-sub">巡る 为你排一条逐帧对照的路线。</p>
      </li>
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
  /* 不写 height：作为 .dock-win 的 flex 子项靠 stretch 拿高度（含 max-height 封顶），
     写死 height:100% 会让计算值非 auto、stretch 失效，内容超高时滚动链断掉 */
  box-sizing: border-box;
  position: relative; /* 历史下拉浮层的定位锚 */
  background: rgb(251 250 245 / 0.96);
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
  font-family: var(--font-serif);
  font-size: 0.88rem;
  letter-spacing: 0.15em;
}
.welcome {
  flex-direction: column; /* .messages li 是 flex 容器，两个 p 要竖排 */
  margin: auto;
  padding: 1.5rem 0.5rem;
  text-align: center;
  font-family: var(--font-serif);
  color: var(--ink-soft);
  font-size: 0.95rem;
  letter-spacing: 0.04em;
  line-height: 2;
}
.welcome p {
  margin: 0;
}
.welcome-sub {
  font-size: 0.8rem;
  color: var(--ink-faint);
}
.bar-actions {
  margin-left: auto;
  display: flex;
  gap: 0.4rem;
  flex-shrink: 0;
}
.bar-btn {
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  color: var(--ink-faint);
  font-size: 0.75rem;
  padding: 0.15rem 0.65rem;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.bar-btn:hover:not(:disabled) {
  color: var(--ink);
  border-color: var(--ink-faint);
}
.bar-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.history-pop {
  position: absolute;
  top: 1.9rem;
  right: 0.6rem;
  z-index: 10;
  width: 15rem;
  max-height: 16rem;
  overflow-y: auto;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow);
  padding: 0.3rem;
}
.history-pop ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.history-pop li {
  display: flex;
  align-items: center;
  gap: 0.2rem;
}
.history-item {
  flex: 1;
  min-width: 0;
  text-align: left;
  background: none;
  border: none;
  border-radius: 6px;
  padding: 0.35rem 0.4rem;
  font-size: 0.82rem;
  color: var(--ink);
  cursor: pointer;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.history-item:hover:not(:disabled) {
  background: var(--paper-deep);
}
.history-item:disabled {
  cursor: default;
}
.history-pop li.current .history-item {
  color: var(--ink-faint);
}
.history-date {
  display: block;
  font-size: 0.7rem;
  color: var(--ink-faint);
}
.history-del {
  flex-shrink: 0;
  border: none;
  background: none;
  border-radius: 4px;
  padding: 0.2rem 0.3rem;
  font-size: 0.75rem;
  color: var(--ink-faint);
  cursor: pointer;
}
.history-del:hover:not(:disabled) {
  color: var(--accent);
}
.history-del:disabled {
  opacity: 0.4;
  cursor: default;
}
.history-empty {
  margin: 0.4rem;
  font-size: 0.8rem;
  color: var(--ink-faint);
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
  flex-shrink: 0; /* 超高时内容必须溢出（出滚动条），而不是被压缩 */
}
.messages .user {
  justify-content: flex-end;
}
.bubble {
  max-width: 85%;
  border-radius: 12px;
  padding: 0.45rem 0.8rem;
  font-size: 0.9rem;
  line-height: 1.7;
  background: var(--card);
  border: 1px solid var(--line); /* 细边让白底气泡在纸底上有形 */
  white-space: pre-wrap;
}
.user .bubble {
  background: var(--ink);
  border-color: var(--ink);
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
  padding: 0.55rem 1rem;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--card);
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
  padding: 0.55rem 1.2rem;
  border: none;
  border-radius: 999px;
  background: var(--accent); /* 主操作用朱：全页唯一的强色焦点 */
  color: var(--paper);
  font-size: 0.9rem;
  cursor: pointer;
  transition: background 0.15s;
}
.composer button:hover:not(:disabled) {
  background: var(--accent-deep);
}
.composer button:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
