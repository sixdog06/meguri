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
}

export interface ChatMessage {
  id: number
  role: string
  content: string
  payload: MessagePayload | null
}
