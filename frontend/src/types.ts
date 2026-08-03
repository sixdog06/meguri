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
 *  当前为距离估算（estimate 恒为 true），fare_yen 留空。 */
export interface TransitLeg {
  from_id: string // 圣地 id（无 id 时为快照内序号）
  to_id: string
  mode: string // walk / drive
  distance_km: number
  duration_minutes: number
  estimate: boolean
  fare_yen: number | null
  cross_day: boolean // true = 每天末尾到次日开头的连接段
}

export interface ItineraryDay {
  day: number
  seichi: SeichiCandidate[]
  legs: TransitLeg[]
}

/** 行程快照：按天组织的圣地序列 + 交通段；预算只留结构。 */
export interface Itinerary {
  work: string | null
  area: string | null
  day_count: number
  days: ItineraryDay[]
  budget: Record<string, unknown> | null
}

/** 每日路线颜色（地图 polyline 与按天视图的色点共用）。 */
export const DAY_COLORS = ['#e11d48', '#2563eb', '#059669', '#d97706', '#7c3aed', '#0891b2', '#be185d']

export function dayColor(day: number): string {
  return DAY_COLORS[(day - 1) % DAY_COLORS.length]
}

export interface ChatMessage {
  id: number
  role: string
  content: string
  payload: MessagePayload | null
}
