/** 行程视图的纯函数助手：按天数据推导展示所需的时间/交通/讲解信息。 */

import type { ItineraryDay, Narration, TransitLeg } from './types'

const legModeLabels: Record<string, string> = {
  walk: '步行',
  drive: '车程',
  transit: '换乘',
}

export function legLabel(leg: TransitLeg): string {
  const mode = legModeLabels[leg.mode] ?? leg.mode
  return `${mode} ${leg.duration_minutes} 分钟 · ${leg.distance_km} km`
}

/** 天内段按圣地 id 对齐（leg.from_id/to_id 引用 seichi.id，重名不断链）。 */
export function legBetween(day: ItineraryDay, i: number): TransitLeg | undefined {
  const from = day.seichi[i]
  const to = day.seichi[i + 1]
  if (!from || !to) return undefined
  return day.legs.find((l) => !l.cross_day && l.from_id === from.id && l.to_id === to.id)
}

/** 跨天连接段：每天末尾到次日开头（挂在出发天 legs 末尾）。 */
export function crossDayLeg(day: ItineraryDay): TransitLeg | undefined {
  return day.legs.find((l) => l.cross_day)
}

/** 单站讲解（检索语料 + citation）。 */
export function narrationOf(day: ItineraryDay, seichiId: string | null): Narration | undefined {
  return day.narrations.find((n) => n.seichi_id === seichiId)
}

/** 当天交通时长合计（分钟）：只含天内段；跨天衔接段单算，不归入任何一天。 */
export function dayTransitMinutes(day: ItineraryDay): number {
  return day.legs.filter((l) => !l.cross_day).reduce((sum, l) => sum + l.duration_minutes, 0)
}

/** 时长的人性化中文格式（"约 2 小时 10 分" / "约 45 分钟"）。 */
export function formatMinutes(min: number): string {
  if (min < 60) return `约 ${min} 分钟`
  const h = Math.floor(min / 60)
  const m = min % 60
  return m ? `约 ${h} 小时 ${m} 分` : `约 ${h} 小时`
}
