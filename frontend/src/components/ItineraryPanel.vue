<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Itinerary, ItineraryDay, SeichiCandidate } from '../types'
import { crossDayLeg, dayTransitMinutes, formatMinutes, legBetween, legLabel, narrationOf } from '../itinerary'
import { dayColor } from '../types'
import { routeUrl } from '../gmaps'
import { exportItineraryPdf } from '../pdf'

const props = defineProps<{
  itinerary: Itinerary | null
  candidates: SeichiCandidate[] // “添加圣地”候选（后端已排除行程内的）
  editing: boolean // 提交后重校验进行中，禁用全部编辑操作
}>()

// 编辑模式：改动先落在本地草稿，点"提交"后 op 列表一次性上抛给 App 顺序应用
// focus：点名站点标题，让地图飞到对应标点
const emit = defineEmits<{ submit: [ops: Record<string, unknown>[]]; collapse: []; focus: [stop: SeichiCandidate] }>()

const activeDay = ref(1)
watch(
  () => props.itinerary,
  (it) => {
    activeDay.value = it?.days[0]?.day ?? 1
  },
)

// ---- 编辑模式本地草稿 ----
const editMode = ref(false)
const draft = ref<ItineraryDay[] | null>(null) // 行程天的本地副本（结构化深拷贝）
const draftCandidates = ref<SeichiCandidate[]>([]) // 候选的本地副本（添加后本地移除）
const pendingOps = ref<Record<string, unknown>[]>([]) // 已暂存的编辑操作，语义与后端 apply_edit 一致

const currentDay = computed(() => props.itinerary?.days.find((d) => d.day === activeDay.value) ?? null)
/** 当前展示的天：编辑模式看草稿，否则看已提交的快照。 */
const displayDay = computed(() =>
  editMode.value && draft.value ? (draft.value.find((d) => d.day === activeDay.value) ?? null) : currentDay.value,
)

// 当天路线在 Google 地图的外链（少于 2 站为 null，模板不展示）
const gmapsDayUrl = computed(() => (displayDay.value ? routeUrl(displayDay.value.seichi) : null))

/** 导出 PDF。 */
function exportPdf() {
  if (!props.itinerary) return
  exportItineraryPdf(props.itinerary).catch(() => {})
}

/** 进入编辑模式：快照当前行程与候选，之后的改动只动本地草稿。 */
function startEdit() {
  if (!props.itinerary) return
  // 深拷贝快照：props 里的 days 是 Vue 响应式 proxy，structuredClone 遇到
  // proxy 会抛 DataCloneError（编辑按钮点了没反应的根因）——数据本就是
  // fetch 来的纯 JSON，用 JSON 往返拷贝即可
  draft.value = JSON.parse(JSON.stringify(props.itinerary.days)) as ItineraryDay[]
  draftCandidates.value = [...props.candidates]
  pendingOps.value = []
  editMode.value = true
}

/** 放弃本地改动，回到只读视图。 */
function cancelEdit() {
  editMode.value = false
  draft.value = null
  draftCandidates.value = []
  pendingOps.value = []
}

/** 提交：把暂存的 op 列表上抛（App 顺序调用后端，逐条重校验），并退出编辑模式。 */
function submitEdit() {
  if (pendingOps.value.length > 0) {
    emit('submit', [...pendingOps.value])
  }
  cancelEdit()
}

const addSelection = ref<Record<number, string>>({}) // 当前天“添加圣地”下拉的选择

/** 加载失败的截图：URL 存在但取不到（404/网络）时同样不展示，不留破图占位。 */
const brokenImages = ref(new Set<string>())

function onPhotoError(s: SeichiCandidate) {
  brokenImages.value = new Set(brokenImages.value).add(String(s.id ?? s.name))
}

/** 改序（草稿内）：上移/下移一位，暂存当天全量新顺序。 */
function moveStop(day: ItineraryDay, i: number, dir: -1 | 1) {
  const j = i + dir
  if (j < 0 || j >= day.seichi.length) return
  ;[day.seichi[i], day.seichi[j]] = [day.seichi[j], day.seichi[i]]
  pendingOps.value.push({ type: 'reorder', day: day.day, seichi_ids: day.seichi.map((s) => s.id) })
}

/** 换天（草稿内）：从原天移除、追加到目标天末尾（与后端 move_day 一致）。 */
function moveDay(s: SeichiCandidate, toDay: number) {
  if (!draft.value) return
  const from = draft.value.find((d) => d.seichi.includes(s))
  const to = draft.value.find((d) => d.day === toDay)
  if (!from || !to || from === to) return
  from.seichi = from.seichi.filter((x) => x !== s)
  to.seichi.push(s)
  pendingOps.value.push({ type: 'move_day', seichi_id: s.id, to_day: toDay })
}

/** 删除（草稿内）。 */
function removeStop(s: SeichiCandidate) {
  if (!draft.value) return
  const day = draft.value.find((d) => d.seichi.includes(s))
  if (!day) return
  day.seichi = day.seichi.filter((x) => x !== s)
  pendingOps.value.push({ type: 'remove', seichi_id: s.id })
}

/** 添加（草稿内）：候选追加到当天末尾，并从本地候选移除（与后端 add 一致）。 */
function addStop(day: ItineraryDay) {
  const id = addSelection.value[day.day]
  const candidate = draftCandidates.value.find((c) => c.id === id)
  if (!id || !candidate) return
  day.seichi.push(candidate)
  draftCandidates.value = draftCandidates.value.filter((c) => c !== candidate)
  pendingOps.value.push({ type: 'add', day: day.day, seichi_id: id })
  addSelection.value[day.day] = ''
}
</script>

<template>
  <aside class="panel">
    <template v-if="itinerary">
      <header class="panel-head">
        <h2>{{ itinerary.work ?? '行程' }}</h2>
        <span class="meta">{{ itinerary.day_count }} 天</span>
        <button v-if="!editMode" class="edit-btn" title="调整站点顺序/换天/增删" @click="startEdit">编辑</button>
        <button class="pdf-btn" title="导出行程 PDF" @click="exportPdf">导出 PDF</button>
        <button class="collapse-btn" title="收起行程" @click="emit('collapse')">◂</button>
      </header>

      <nav class="day-tabs">
        <button
          v-for="day in itinerary.days"
          :key="day.day"
          :class="{ active: activeDay === day.day }"
          @click="activeDay = day.day"
        >
          <span class="day-dot" :style="{ background: dayColor(day.day) }" />
          Day {{ day.day }} · {{ day.seichi.length }} 站<template v-if="dayTransitMinutes(day)"> · {{ formatMinutes(dayTransitMinutes(day)) }}</template>
        </button>
      </nav>

      <div v-if="displayDay" class="timeline">
        <!-- 当天路线 Google 地图外链（纯 URL 协议，少于 2 站不显示） -->
        <a v-if="gmapsDayUrl" :href="gmapsDayUrl" target="_blank" rel="noopener" class="gmaps-link">
          在 Google 地图打开今日路线 →
        </a>

        <template v-for="(s, i) in displayDay.seichi" :key="s.id ?? s.name">
          <div class="t-stop">
            <div class="t-rail"><span class="t-node" :style="{ background: dayColor(displayDay.day) }" /></div>
            <div class="t-card">
              <div class="t-head">
                <span class="t-name" title="在地图上定位" @click="emit('focus', s)">{{ s.name }}</span>
              </div>
              <!-- 对照截图（anitabi 参考图）：有图且能加载才展示，URL 失效也不留破图 -->
              <img
                v-if="s.image && !brokenImages.has(String(s.id ?? s.name))"
                class="t-photo"
                :src="s.image"
                :alt="s.name"
                loading="lazy"
                @error="onPhotoError(s)"
              />
              <p v-if="narrationOf(displayDay, s.id)" class="narration">
                {{ narrationOf(displayDay, s.id)!.text }}
                <span v-if="narrationOf(displayDay, s.id)!.citation" class="citation">
                  <template v-if="narrationOf(displayDay, s.id)!.citation!.url">
                    来源：<a :href="narrationOf(displayDay, s.id)!.citation!.url!" target="_blank" rel="noopener">{{ narrationOf(displayDay, s.id)!.citation!.source }}</a>
                  </template>
                  <template v-else>
                    来源：{{ narrationOf(displayDay, s.id)!.citation!.source }}
                  </template>
                </span>
              </p>
              <!-- 编辑操作只在编辑模式出现，改动先落本地草稿，提交后才重规划 -->
              <div v-if="editMode" class="edit-ops">
                <button title="上移" :disabled="i === 0" @click="moveStop(displayDay!, i, -1)">↑</button>
                <button title="下移" :disabled="i === displayDay!.seichi.length - 1" @click="moveStop(displayDay!, i, 1)">↓</button>
                <select
                  title="换天"
                  :value="displayDay!.day"
                  @change="moveDay(s, Number(($event.target as HTMLSelectElement).value))"
                >
                  <option v-for="d in itinerary!.day_count" :key="d" :value="d">D{{ d }}</option>
                </select>
                <button title="删除" @click="removeStop(s)">删</button>
              </div>
            </div>
          </div>
          <!-- 路段是按站序算的，编辑模式下站序已变但未重校验，故只读模式才展示 -->
          <div v-if="!editMode && legBetween(displayDay, i)" class="t-leg" :title="legBetween(displayDay, i)!.note ?? ''">
            <div class="t-rail"><span class="t-line" /></div>
            <div class="t-leg-label">
              {{ legLabel(legBetween(displayDay, i)!) }}
              <span v-if="legBetween(displayDay, i)!.estimate" class="estimate">估算</span>
              <span v-if="legBetween(displayDay, i)!.degraded" class="degraded">降级</span>
            </div>
          </div>
        </template>

        <p v-if="!editMode && crossDayLeg(displayDay)" class="cross-day" :title="crossDayLeg(displayDay)!.note ?? ''">
          → 次日：{{ legLabel(crossDayLeg(displayDay)!) }}
          <span v-if="crossDayLeg(displayDay)!.estimate" class="estimate">估算</span>
          <span v-if="crossDayLeg(displayDay)!.degraded" class="degraded">降级</span>
        </p>

        <div v-if="editMode && draftCandidates.length" class="add-stop">
          <select v-model="addSelection[displayDay.day]">
            <option value="" disabled>添加圣地…</option>
            <option v-for="c in draftCandidates" :key="c.id ?? c.name" :value="c.id ?? ''">{{ c.name }}</option>
          </select>
          <button :disabled="!addSelection[displayDay.day]" @click="addStop(displayDay!)">添加</button>
        </div>
      </div>

      <!-- 编辑模式底栏：提交后 App 顺序应用暂存的 op 并重规划 -->
      <div v-if="editMode" class="edit-bar">
        <span class="pending-count">{{ pendingOps.length ? `${pendingOps.length} 项待提交` : '尚未修改' }}</span>
        <button class="cancel-btn" :disabled="editing" @click="cancelEdit">取消</button>
        <button class="submit-btn" :disabled="editing || pendingOps.length === 0" @click="submitEdit">
          {{ editing ? '规划中…' : '提交并重新规划' }}
        </button>
      </div>

      <!-- 编辑等待遮罩：重校验需数秒（live 交通），明确提示而非"卡死" -->
      <div v-if="editing" class="edit-wait">
        <span class="spinner" />
        <span>稍等，正在重新校验行程…</span>
      </div>
    </template>

    <div v-else class="placeholder">
      <p>在下方告诉 Meguri 想去哪里巡礼，</p>
      <p>行程时间轴会出现在这里。</p>
    </div>
  </aside>
</template>

<style scoped>
.panel {
  position: relative; /* 编辑等待遮罩的定位锚点 */
  background: rgb(251 250 245 / 0.96);
  backdrop-filter: blur(8px);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.edit-wait {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  background: rgb(251 250 245 / 0.78);
  backdrop-filter: blur(2px);
  color: var(--ink-soft);
  font-size: 0.9rem;
  letter-spacing: 0.03em;
}
.spinner {
  width: 1rem;
  height: 1rem;
  border: 2px solid var(--line);
  border-top-color: var(--ink-soft);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.panel-head {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  padding: 0.85rem 1rem 0.3rem;
}
.panel-head h2 {
  font-family: var(--font-serif);
  font-size: 1.05rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  margin: 0;
}
.meta {
  color: var(--ink-faint);
  font-size: 0.82rem;
}
.pdf-btn {
  margin-left: auto;
}
.collapse-btn,
.edit-btn,
.pdf-btn {
  background: none;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--ink-faint);
  font-size: 0.75rem;
  padding: 0.15rem 0.65rem;
  cursor: pointer;
  transition:
    color 0.15s,
    border-color 0.15s;
}
.collapse-btn:hover,
.edit-btn:hover,
.pdf-btn:hover {
  color: var(--ink);
  border-color: var(--ink-faint);
}
/* 编辑模式底栏：待提交数 + 取消/提交 */
.edit-bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 1rem;
  border-top: 1px solid var(--line);
}
.pending-count {
  flex: 1;
  color: var(--ink-faint);
  font-size: 0.78rem;
}
.cancel-btn,
.submit-btn {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: transparent;
  color: var(--ink-soft);
  font-size: 0.8rem;
  padding: 0.3rem 0.8rem;
  cursor: pointer;
  transition:
    color 0.15s,
    border-color 0.15s,
    background 0.15s;
}
.submit-btn {
  background: var(--accent); /* 与发送按钮一致：主操作统一用朱 */
  border-color: var(--accent);
  color: var(--paper);
}
.cancel-btn:hover:not(:disabled) {
  color: var(--ink);
  border-color: var(--ink-faint);
}
.cancel-btn:disabled,
.submit-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.day-tabs {
  display: flex;
  gap: 0.4rem;
  padding: 0.45rem 1rem;
  overflow-x: auto;
  border-bottom: 1px solid var(--line);
}
.day-tabs button {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink-soft);
  border-radius: 999px;
  padding: 0.28rem 0.8rem;
  font-size: 0.8rem;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background 0.15s,
    color 0.15s,
    border-color 0.15s;
}
.day-tabs button:hover {
  border-color: var(--ink-faint);
  color: var(--ink);
}
.day-tabs button.active {
  background: var(--ink);
  color: var(--paper);
  border-color: var(--ink);
}
.day-dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  display: inline-block;
}
.timeline {
  flex: 1;
  overflow-y: auto;
  padding: 0.85rem 1rem 4rem;
}
.t-stop,
.t-leg {
  display: flex;
  gap: 0.6rem;
  align-items: stretch;
}
.t-rail {
  width: 0.9rem;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.t-node {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  margin-top: 0.75rem;
  flex-shrink: 0;
  box-shadow: 0 0 0 2px var(--paper);
}
.t-rail::after {
  content: '';
  flex: 1;
  width: 1px;
  background: var(--line);
}
.t-leg .t-rail::after {
  background: transparent;
}
.t-line {
  flex: 1;
  width: 1px;
  background: var(--line);
  min-height: 1rem;
}
.t-card {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 0.55rem 0.8rem;
  margin: 0.3rem 0;
  background: var(--card);
  transition: border-color 0.15s;
}
.t-card:hover {
  border-color: var(--line-strong);
}
.t-photo {
  display: block;
  width: 100%;
  max-height: 9rem;
  object-fit: cover;
  border-radius: 6px;
  margin-top: 0.4rem;
}
.t-head {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}
.t-name {
  font-weight: 700;
  font-size: 0.92rem;
  cursor: pointer; /* 点名可在地图上定位对应标点 */
  transition: color 0.15s;
}
.t-name:hover {
  color: var(--accent);
}
.t-leg-label {
  color: var(--ink-faint);
  font-size: 0.78rem;
  padding: 0.3rem 0;
}
.estimate {
  background: var(--amber-bg);
  color: var(--amber-ink);
  border-radius: 4px;
  padding: 0 0.3rem;
  font-size: 0.7rem;
  margin-left: 0.25rem;
}
.degraded {
  background: var(--accent-soft-bg);
  color: var(--accent);
  border-radius: 4px;
  padding: 0 0.3rem;
  font-size: 0.7rem;
  margin-left: 0.25rem;
}
.narration {
  margin: 0.3rem 0 0;
  font-family: var(--font-serif);
  color: var(--ink-soft);
  font-size: 0.85rem;
  line-height: 1.7;
}
.citation {
  font-family: var(--font-sans);
  color: var(--ink-faint);
  font-size: 0.72rem;
  margin-left: 0.4rem;
}
.edit-ops {
  display: inline-flex;
  gap: 0.3rem;
  margin-top: 0.3rem;
}
.edit-ops button,
.edit-ops select {
  font-size: 0.72rem;
  padding: 0.05rem 0.35rem;
  color: var(--ink-faint);
  background: transparent;
  border: 1px solid var(--line);
  border-radius: 5px;
  cursor: pointer;
  transition:
    color 0.15s,
    border-color 0.15s;
}
.edit-ops button:hover,
.edit-ops select:hover {
  color: var(--ink);
  border-color: var(--ink-faint);
}
.edit-ops button:disabled,
.edit-ops select:disabled {
  opacity: 0.4;
  cursor: default;
}
.cross-day {
  color: var(--ink-faint);
  font-size: 0.8rem;
  margin: 0.5rem 0 0 1.5rem; /* 与去掉时间列后的站点卡左缘对齐 */
}
.gmaps-link {
  display: block;
  color: var(--ink-faint);
  font-size: 0.78rem;
  text-decoration: none;
  margin: 0 0 0.6rem; /* 置于时间轴顶部，左对齐 */
  transition: color 0.15s;
}
.gmaps-link:hover {
  color: var(--ink);
}
.add-stop {
  margin: 0.75rem 0 0 1.5rem; /* 与去掉时间列后的站点卡左缘对齐 */
  display: flex;
  gap: 0.4rem;
  font-size: 0.84rem;
}
.add-stop select {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--card);
  padding: 0.3rem 0.4rem;
  color: var(--ink);
}
.add-stop button {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: transparent;
  color: var(--ink-soft);
  padding: 0.3rem 0.7rem;
  cursor: pointer;
}
.add-stop button:hover:not(:disabled) {
  color: var(--ink);
  border-color: var(--ink-faint);
}
.add-stop button:disabled {
  opacity: 0.4;
  cursor: default;
}
.placeholder {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--ink-faint);
  font-family: var(--font-serif);
  font-size: 0.92rem;
  letter-spacing: 0.05em;
  line-height: 2;
}
.placeholder p {
  margin: 0;
}
</style>
