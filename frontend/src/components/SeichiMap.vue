<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import markerIcon2xUrl from 'leaflet/dist/images/marker-icon-2x.png'
import markerIconUrl from 'leaflet/dist/images/marker-icon.png'
import markerShadowUrl from 'leaflet/dist/images/marker-shadow.png'
import type { Itinerary, SeichiCandidate } from '../types'
import { dayColor } from '../types'
import { pointUrl } from '../gmaps'

/** 行程面板点名的定位目标：seq 递增保证同一点重复点击也能触发。 */
export interface MapFocus {
  id: string
  lat: number
  lng: number
  seq: number
}

const props = defineProps<{ seichi: SeichiCandidate[]; itinerary?: Itinerary | null; focus?: MapFocus | null }>()

// vite 下 Leaflet 默认 marker 图标路径会丢，显式绑定打包后的资源
L.Marker.prototype.options.icon = L.icon({
  iconUrl: markerIconUrl,
  iconRetinaUrl: markerIcon2xUrl,
  shadowUrl: markerShadowUrl,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
})

const mapEl = ref<HTMLDivElement | null>(null)
let map: L.Map | null = null
let markers: L.LayerGroup | null = null
// 标点按圣地 id（无 id 用名称）索引，供面板点名后定位并打开弹窗
const markerByKey = new Map<string, L.Marker>()

function markerKey(s: SeichiCandidate): string {
  return String(s.id ?? s.name)
}

function escapeHtml(text: string): string {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

function popupHtml(s: SeichiCandidate): string {
  const parts = [`<strong>${escapeHtml(s.name)}</strong>`]
  if (s.image) {
    parts.push(`<img src="${escapeHtml(s.image)}" alt="${escapeHtml(s.name)}" style="max-width:220px;display:block;margin:4px 0" />`)
  }
  if (typeof s.ep === 'number') {
    // 集数为数字才显示“第 N 集”（ep 也可能是 "OST" 等字符串）
    const at =
      typeof s.ep_seconds === 'number'
        ? ` ${Math.floor(s.ep_seconds / 60)}:${String(s.ep_seconds % 60).padStart(2, '0')}`
        : ''
    parts.push(`<div>出处：第 ${s.ep} 集${at}</div>`)
  } else if (s.ep) {
    parts.push(`<div>出处：${escapeHtml(String(s.ep))}</div>`)
  }
  if (s.origin) {
    // anitabi 截图遵循 CC BY-NC-SA，需标注来源并链接 originURL
    const origin = escapeHtml(s.origin)
    parts.push(
      s.origin_url
        ? `<div>截图来源：<a href="${escapeHtml(s.origin_url)}" target="_blank" rel="noopener">${origin}</a></div>`
        : `<div>截图来源：${origin}</div>`,
    )
  }
  // Google 地图外链（纯 URL 协议，新标签页打开）
  parts.push(`<div><a href="${pointUrl(s.lat, s.lng)}" target="_blank" rel="noopener">在 Google 地图打开 →</a></div>`)
  return parts.join('')
}

function renderMarkers() {
  if (!map || !markers) return
  markers.clearLayers()
  markerByKey.clear()
  const points: L.LatLngExpression[] = []
  if (props.itinerary) {
    // 行程视图：每天一条路线连线（按天区分颜色）+ 圣地标点
    for (const day of props.itinerary.days) {
      const dayPoints = day.seichi.map((s): L.LatLngExpression => [s.lat, s.lng])
      for (const s of day.seichi) {
        markerByKey.set(markerKey(s), L.marker([s.lat, s.lng]).bindPopup(popupHtml(s)).addTo(markers))
      }
      if (dayPoints.length > 1) {
        L.polyline(dayPoints, { color: dayColor(day.day), weight: 4, opacity: 0.8 }).addTo(markers)
      }
      points.push(...dayPoints)
    }
  } else {
    for (const s of props.seichi) {
      markerByKey.set(markerKey(s), L.marker([s.lat, s.lng]).bindPopup(popupHtml(s)).addTo(markers))
      points.push([s.lat, s.lng])
    }
  }
  if (points.length > 0) {
    map.fitBounds(L.latLngBounds(points), { padding: [30, 30], maxZoom: 15 })
  }
}

onMounted(() => {
  if (!mapEl.value) return
  map = L.map(mapEl.value).setView([36.2, 138.25], 5) // 默认俯瞰日本列岛
  // CARTO Positron 淡色瓦片：底图几乎无色，路线与标点是唯一视觉焦点
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 20,
  }).addTo(map)
  markers = L.layerGroup().addTo(map)
  renderMarkers()
})

watch(() => [props.seichi, props.itinerary], renderMarkers)

// 面板点名 → 飞到对应标点并打开弹窗（标点可能因行程重建尚未渲染，拿不到就只飞过去）
watch(
  () => props.focus,
  (focus) => {
    if (!map || !focus) return
    map.flyTo([focus.lat, focus.lng], Math.max(map.getZoom(), 15), { duration: 0.6 })
    markerByKey.get(focus.id)?.openPopup()
  },
)

onBeforeUnmount(() => {
  map?.remove()
  map = null
})
</script>

<template>
  <div ref="mapEl" class="map" />
</template>

<style scoped>
.map {
  width: 100%;
  height: 100%;
  min-height: 420px;
  border-radius: 8px;
}
</style>
