/** Google 地图 URL 协议构造（纯 URL，无需 API key）。 */

// Google 地图网页版路线最多约 9 个途经点（waypoints），超出部分直接截断。
const MAX_WAYPOINTS = 9

/** 单点搜索链接：在 Google 地图打开某个坐标。 */
export function pointUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`
}

/** 多点路线链接：首站为 origin、末站为 destination，中间站为 waypoints（| 分隔）。
 *  少于 2 个点时返回 null（调用方不展示入口）。 */
export function routeUrl(stops: { lat: number; lng: number }[]): string | null {
  if (stops.length < 2) return null
  const [first, ...rest] = stops
  const last = rest[rest.length - 1]
  const middle = rest.slice(0, -1).slice(0, MAX_WAYPOINTS)
  let url = `https://www.google.com/maps/dir/?api=1&origin=${first.lat},${first.lng}&destination=${last.lat},${last.lng}`
  if (middle.length > 0) {
    url += `&waypoints=${middle.map((s) => `${s.lat},${s.lng}`).join('|')}`
  }
  return url
}
