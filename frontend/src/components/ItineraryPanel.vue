<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Itinerary, ItineraryDay, SeichiCandidate } from '../types'
import { checkOf, crossDayLeg, legBetween, legLabel, narrationOf } from '../itinerary'
import { dayColor } from '../types'

const props = defineProps<{
  itinerary: Itinerary | null
  candidates: SeichiCandidate[] // “添加圣地”候选（后端已排除行程内的）
  editing: boolean // 一次编辑请求进行中，禁用全部编辑操作
}>()

// 编辑操作统一上抛给 App（postEdit 后端自动重跑校验/预算/讲解，返回新快照）
const emit = defineEmits<{ edit: [body: Record<string, unknown>]; collapse: [] }>()

const activeDay = ref(1)
watch(
  () => props.itinerary,
  (it) => {
    activeDay.value = it?.days[0]?.day ?? 1
  },
)
const currentDay = computed(() => props.itinerary?.days.find((d) => d.day === activeDay.value) ?? null)

const addSelection = ref<Record<number, string>>({}) // 当前天“添加圣地”下拉的选择

/** 改序：上移/下移一位（按钮式，不做拖拽）。 */
function moveStop(day: ItineraryDay, i: number, dir: -1 | 1) {
  const ids = day.seichi.map((s) => s.id as string)
  const j = i + dir
  if (j < 0 || j >= ids.length) return
  ;[ids[i], ids[j]] = [ids[j], ids[i]]
  emit('edit', { type: 'reorder', day: day.day, seichi_ids: ids })
}
</script>

<template>
  <aside class="panel">
    <template v-if="itinerary">
      <header class="panel-head">
        <h2>{{ itinerary.work ?? '行程' }}</h2>
        <span class="meta">{{ itinerary.day_count }} 天</span>
        <button class="collapse-btn" title="收起行程" @click="emit('collapse')">◂</button>
      </header>

      <details v-if="itinerary.budget" class="budget">
        <summary :class="{ over: itinerary.budget.over_budget }">
          ¥{{ itinerary.budget.total_yen.toLocaleString() }}<template v-if="itinerary.budget.limit_yen !== null"> / 上限 ¥{{ itinerary.budget.limit_yen.toLocaleString() }}</template>
          <span v-if="itinerary.budget.over_budget" class="over-alert">⚠ 超支</span>
        </summary>
        <p v-if="itinerary.budget.over_budget" class="over-alert">⚠ {{ itinerary.budget.alert }}</p>
        <p v-for="(item, i) in itinerary.budget.transit" :key="'t' + i" class="budget-item">
          交通 · {{ item.label }}：{{ item.amount_yen === null ? '未计价' : `¥${item.amount_yen.toLocaleString()}` }}
        </p>
        <p v-for="(item, i) in itinerary.budget.admission" :key="'a' + i" class="budget-item">
          门票 · {{ item.label }}：{{ item.amount_yen === null ? '未计价' : `¥${item.amount_yen.toLocaleString()}` }}
        </p>
        <p v-if="itinerary.budget.unpriced_count" class="unpriced">
          {{ itinerary.budget.unpriced_count }} 项未计价（票价/门票数据缺失，未计入合计与超支判断）
        </p>
      </details>

      <nav class="day-tabs">
        <button
          v-for="day in itinerary.days"
          :key="day.day"
          :class="{ active: activeDay === day.day }"
          @click="activeDay = day.day"
        >
          <span class="day-dot" :style="{ background: dayColor(day.day) }" />
          Day {{ day.day }} · {{ day.seichi.length }} 站
        </button>
      </nav>

      <div v-if="currentDay" class="timeline">
        <template v-for="(s, i) in currentDay.seichi" :key="s.id ?? s.name">
          <div class="t-stop">
            <div class="t-time">{{ checkOf(currentDay, s.id)?.arrive_time ?? '--:--' }}</div>
            <div class="t-rail"><span class="t-node" :style="{ background: dayColor(currentDay.day) }" /></div>
            <div class="t-card">
              <div class="t-head">
                <span class="t-name">{{ s.name }}</span>
                <span v-if="checkOf(currentDay, s.id)?.open === false" class="warn" :title="checkOf(currentDay, s.id)!.note ?? ''">
                  可能闭馆
                </span>
              </div>
              <p v-if="narrationOf(currentDay, s.id)" class="narration">
                {{ narrationOf(currentDay, s.id)!.text }}
                <span v-if="narrationOf(currentDay, s.id)!.citation" class="citation">
                  语料：{{ narrationOf(currentDay, s.id)!.citation!.source }}
                </span>
              </p>
              <div class="edit-ops">
                <button title="上移" :disabled="editing || i === 0" @click="moveStop(currentDay!, i, -1)">↑</button>
                <button title="下移" :disabled="editing || i === currentDay!.seichi.length - 1" @click="moveStop(currentDay!, i, 1)">↓</button>
                <select
                  title="换天"
                  :value="currentDay!.day"
                  :disabled="editing"
                  @change="emit('edit', { type: 'move_day', seichi_id: s.id, to_day: Number(($event.target as HTMLSelectElement).value) })"
                >
                  <option v-for="d in itinerary!.day_count" :key="d" :value="d">D{{ d }}</option>
                </select>
                <button title="删除" :disabled="editing" @click="emit('edit', { type: 'remove', seichi_id: s.id })">删</button>
              </div>
            </div>
          </div>
          <div v-if="legBetween(currentDay, i)" class="t-leg" :title="legBetween(currentDay, i)!.note ?? ''">
            <div class="t-time" />
            <div class="t-rail"><span class="t-line" /></div>
            <div class="t-leg-label">
              {{ legLabel(legBetween(currentDay, i)!) }}
              <span v-if="legBetween(currentDay, i)!.estimate" class="estimate">估算</span>
              <span v-if="legBetween(currentDay, i)!.degraded" class="degraded">降级</span>
            </div>
          </div>
        </template>

        <p v-if="crossDayLeg(currentDay)" class="cross-day" :title="crossDayLeg(currentDay)!.note ?? ''">
          → 次日：{{ legLabel(crossDayLeg(currentDay)!) }}
          <span v-if="crossDayLeg(currentDay)!.estimate" class="estimate">估算</span>
          <span v-if="crossDayLeg(currentDay)!.degraded" class="degraded">降级</span>
        </p>

        <div v-if="candidates.length" class="add-stop">
          <select v-model="addSelection[currentDay.day]" :disabled="editing">
            <option value="" disabled>添加圣地…</option>
            <option v-for="c in candidates" :key="c.id ?? c.name" :value="c.id ?? ''">{{ c.name }}</option>
          </select>
          <button
            :disabled="editing || !addSelection[currentDay.day]"
            @click="emit('edit', { type: 'add', day: currentDay!.day, seichi_id: addSelection[currentDay!.day] })"
          >
            添加
          </button>
        </div>
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
  background: rgb(250 249 245 / 0.96);
  backdrop-filter: blur(8px);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
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
.collapse-btn {
  margin-left: auto;
  background: none;
  border: 1px solid var(--line);
  border-radius: 5px;
  color: var(--ink-faint);
  font-size: 0.75rem;
  padding: 0 0.4rem;
  cursor: pointer;
  transition:
    color 0.15s,
    border-color 0.15s;
}
.collapse-btn:hover {
  color: var(--ink);
  border-color: var(--ink-faint);
}
.budget {
  margin: 0.35rem 1rem;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0.4rem 0.75rem;
  font-size: 0.84rem;
  background: #fffdf9;
}
.budget summary {
  cursor: pointer;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.budget summary.over,
.over-alert {
  color: var(--accent);
}
.over-alert {
  font-weight: 700;
  margin: 0.25rem 0;
}
.budget-item {
  margin: 0.1rem 0;
  color: var(--ink-soft);
}
.unpriced {
  color: var(--amber-ink);
  font-size: 0.78rem;
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
.t-time {
  width: 2.9rem;
  flex-shrink: 0;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--ink-faint);
  font-size: 0.8rem;
  padding-top: 0.7rem;
}
.t-leg .t-time {
  padding-top: 0;
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
  border-radius: 10px;
  padding: 0.5rem 0.75rem;
  margin: 0.3rem 0;
  background: #fffdf9;
  transition: border-color 0.15s;
}
.t-card:hover {
  border-color: #d5cfbf;
}
.t-head {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}
.t-name {
  font-weight: 700;
  font-size: 0.92rem;
}
.warn {
  background: var(--accent-soft-bg);
  color: var(--accent);
  border-radius: 4px;
  padding: 0 0.35rem;
  font-size: 0.72rem;
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
  color: #55504a;
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
  margin: 0.5rem 0 0 4.4rem;
}
.add-stop {
  margin: 0.75rem 0 0 4.4rem;
  display: flex;
  gap: 0.4rem;
  font-size: 0.84rem;
}
.add-stop select {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fffdf9;
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
