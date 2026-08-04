/** 前后端共享的 API 类型。 */

/** 候选圣地：名称、坐标、对照截图引用、出处（集数+截图来源）。 */
export interface SeichiCandidate {
  id: string | null
  name: string
  work: string | null
  area: string | null
  lat: number
  lng: number
  image: string | null
  ep: number | string | null
  ep_seconds: number | null
  origin: string | null
  origin_url: string | null
}

/** assistant 消息的结构化负载：按工具名收集（见后端 Tool 协议约定）。 */
export interface MessagePayload {
  search_seichi?: SeichiCandidate[]
  plan_itinerary?: Itinerary
}

/** 交通段：相邻两个圣地之间（或天与天之间）的衔接。
 *  schema（mode/duration_minutes/fare_yen/estimate）即 #6 OTP 的数据契约；
 *  OTP 查询失败/未覆盖时保留估算并 degraded=true 显式降级。 */
export interface TransitLeg {
  from_id: string // 圣地 id（无 id 时为快照内序号）
  to_id: string
  mode: string // walk / drive（估算）/ transit（OTP 真实）
  distance_km: number
  duration_minutes: number
  estimate: boolean
  fare_yen: number | null
  cross_day: boolean // true = 每天末尾到次日开头的连接段
  degraded: boolean // true = 交通查询失败/未覆盖，已保留估算（降级提示）
  note: string | null
}

/** 单站时间校验：计划到达时间 + 开放时间（open 为 null = 未知不误标）。 */
export interface StopCheck {
  seichi_id: string
  arrive_time: string
  open: boolean | null
  note: string | null
}

/** 单站讲解（#8 Storyteller）：检索语料原文片段 + citation。 */
export interface Narration {
  seichi_id: string
  text: string
  citation: { chunk_id: string; source: string } | null
}

export interface ItineraryDay {
  day: number
  seichi: SeichiCandidate[]
  legs: TransitLeg[]
  checks: StopCheck[]
  narrations: Narration[]
}

/** 预算明细项：amount_yen 为 null = 未计价（不计入合计，不静默当 0）。 */
export interface BudgetItem {
  label: string
  amount_yen: number | null
}

/** 预算报告：总计、交通/门票分项、超支告警（#7 确定性预算服务）。 */
export interface Budget {
  limit_yen: number | null
  total_yen: number // 已计价合计
  over_budget: boolean
  alert: string | null
  transit: BudgetItem[]
  admission: BudgetItem[]
  unpriced_count: number // 未计价项数
}

/** 行程快照：按天组织的圣地序列 + 交通段 + 预算报告（#7 预算服务产出）。 */
export interface Itinerary {
  work: string | null
  area: string | null
  day_count: number
  days: ItineraryDay[]
  budget: Budget | null
}

/** 每日路线颜色（地图 polyline 与按天视图的色点共用）。低饱和传统色，呼应整体日式极简基调。 */
export const DAY_COLORS = ['#b5432f', '#4a6b82', '#6f8560', '#b98a3e', '#7d6b8f', '#4f8280', '#a85d72']

export function dayColor(day: number): string {
  return DAY_COLORS[(day - 1) % DAY_COLORS.length]
}

export interface ChatMessage {
  id: number
  role: string
  content: string
  payload: MessagePayload | null
}
