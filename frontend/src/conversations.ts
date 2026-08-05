/** 会话历史：会话 id + 标题存浏览器 localStorage（用户本地），
 *  消息/行程内容仍从后端按 id 拉取，不重复存储。 */

import type { ConversationMeta } from './types'

const HISTORY_KEY = 'meguri_conversations'

/** 读取本地历史（按 updatedAt 倒序）；数据损坏时当作空历史，不抛错。 */
export function loadHistory(): ConversationMeta[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    if (!raw) return []
    const list = JSON.parse(raw) as ConversationMeta[]
    return Array.isArray(list) ? list : []
  } catch {
    return []
  }
}

function saveHistory(list: ConversationMeta[]): ConversationMeta[] {
  list.sort((a, b) => b.updatedAt - a.updatedAt)
  localStorage.setItem(HISTORY_KEY, JSON.stringify(list))
  return list
}

/** upsert 一条历史：已存在则刷新时间（和新标题），不存在则追加；返回排序后的新列表。 */
export function touchConversation(id: string, title?: string): ConversationMeta[] {
  const list = loadHistory()
  const found = list.find((c) => c.id === id)
  if (found) {
    found.updatedAt = Date.now()
    if (title) found.title = title
  } else {
    list.push({ id, title: title ?? '新主题', updatedAt: Date.now() })
  }
  return saveHistory(list)
}

/** 删除一条历史（只删本地记录，后端会话数据保留）。 */
export function removeConversation(id: string): ConversationMeta[] {
  return saveHistory(loadHistory().filter((c) => c.id !== id))
}

/** 从历史消息推导标题：首条 user 消息截断；没有则兜底。 */
export function deriveTitle(messages: { role: string; content: string }[]): string {
  const first = messages.find((m) => m.role === 'user')
  if (!first) return '新主题'
  const text = first.content.replace(/\s+/g, ' ').trim()
  return text.length > 20 ? `${text.slice(0, 20)}…` : text || '新主题'
}
