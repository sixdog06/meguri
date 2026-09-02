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

/** 单站时间校验：计划到达时间。 */
export interface StopCheck {
  seichi_id: string
  arrive_time: string
}

/** 单站讲解（#8 Storyteller）：由站点元数据生成 + 来源署名（anitabi 截图来源）。 */
export interface Narration {
  seichi_id: string
  text: string
  citation: { source: string; url: string | null } | null
}

export interface ItineraryDay {
  day: number
  seichi: SeichiCandidate[]
  legs: TransitLeg[]
  checks: StopCheck[]
  narrations: Narration[]
}

/** 行程快照：按天组织的圣地序列 + 交通段。 */
export interface Itinerary {
  work: string | null
  area: string | null
  day_count: number
  days: ItineraryDay[]
}

/** 每日路线颜色（地图 polyline 与按天视图的色点共用）。低饱和传统色，呼应整体基调；
 *  首日与强调色同族（茜），其余为传统色中的灰调蓝绿紫。 */
export const DAY_COLORS = ['#b7282e', '#426579', '#769164', '#bf783a', '#867ba9', '#5c9291', '#a25768']

export function dayColor(day: number): string {
  return DAY_COLORS[(day - 1) % DAY_COLORS.length]
}

export interface ChatMessage {
  id: number
  role: string
  content: string
  payload: MessagePayload | null
}

/** 本地历史里一条会话的元数据（存在浏览器 localStorage；内容仍从后端按 id 拉取）。 */
export interface ConversationMeta {
  id: string
  title: string // 首条 user 消息截断；兜底“新主题”
  updatedAt: number // 毫秒时间戳，列表按它倒序
}
