<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Feature } from 'geojson'
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

const mapEl = ref<HTMLDivElement | null>(null)
let map: maplibregl.Map | null = null
// 样式加载完成后才能加 source/layer；此前的渲染在 load 后统一补一次
let styleReady = false
// 标点按圣地 id（无 id 用名称）索引，供面板点名后定位并打开弹窗
const markerByKey = new Map<string, maplibregl.Marker>()

const ROUTE_SOURCE = 'itinerary-route'

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

function clearMarkers() {
  for (const marker of markerByKey.values()) marker.remove()
  markerByKey.clear()
}

function addMarker(s: SeichiCandidate, color?: string) {
  if (!map) return
  const popup = new maplibregl.Popup({ maxWidth: '260px' }).setHTML(popupHtml(s))
  const marker = new maplibregl.Marker(color ? { color } : {})
    .setLngLat([s.lng, s.lat])
    .setPopup(popup)
    .addTo(map)
  markerByKey.set(markerKey(s), marker)
}

function renderMarkers() {
  if (!map) return
  if (!styleReady) return // 样式未加载完，load 后会统一补一次渲染
  clearMarkers()
  const bounds = new maplibregl.LngLatBounds()
  let hasPoints = false
  const routeFeatures: Feature[] = []
  if (props.itinerary) {
    // 行程视图：每天一条路线连线（按天区分颜色）+ 同天色标点
    for (const day of props.itinerary.days) {
      const color = dayColor(day.day)
      for (const s of day.seichi) {
        addMarker(s, color)
        bounds.extend([s.lng, s.lat])
        hasPoints = true
      }
      if (day.seichi.length > 1) {
        routeFeatures.push({
          type: 'Feature',
          properties: { color },
          geometry: {
            type: 'LineString',
            coordinates: day.seichi.map((s) => [s.lng, s.lat]),
          },
        })
      }
    }
  } else {
    for (const s of props.seichi) {
      addMarker(s)
      bounds.extend([s.lng, s.lat])
      hasPoints = true
    }
  }
  const source = map.getSource(ROUTE_SOURCE) as maplibregl.GeoJSONSource | undefined
  source?.setData({ type: 'FeatureCollection', features: routeFeatures })
  if (hasPoints) {
    map.fitBounds(bounds, { padding: 30, maxZoom: 15 })
  }
}

onMounted(() => {
  if (!mapEl.value) return
  map = new maplibregl.Map({
    container: mapEl.value,
    // OpenFreeMap 矢量瓦片（OSM 数据）：免费免 key 无用量限制，可自托管。
    // bright 彩色风格，路线与标点用 dayColor 叠加其上。备选：positron（浅灰极简）。
    style: 'https://tiles.openfreemap.org/styles/bright',
    center: [138.25, 36.2], // 默认俯瞰日本列岛
    zoom: 4.5,
    attributionControl: {
      compact: true,
      customAttribution:
        '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
    },
  })
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')
  map.on('load', () => {
    if (!map) return
    map.addSource(ROUTE_SOURCE, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    })
    map.addLayer({
      id: ROUTE_SOURCE,
      type: 'line',
      source: ROUTE_SOURCE,
      paint: {
        'line-color': ['get', 'color'],
        'line-width': 4,
        'line-opacity': 0.8,
      },
    })
    styleReady = true
    renderMarkers()
  })
})

watch(
  () => [props.seichi, props.itinerary],
  () => renderMarkers(),
)

// 面板点名 → 飞到对应标点并打开弹窗（标点可能因行程重建尚未渲染，拿不到就只飞过去）
watch(
  () => props.focus,
  (focus) => {
    if (!map || !focus) return
    map.flyTo({ center: [focus.lng, focus.lat], zoom: Math.max(map.getZoom(), 15), duration: 600 })
    const marker = markerByKey.get(focus.id)
    const popup = marker?.getPopup()
    // togglePopup 会把已打开的弹窗关掉，重复点名同一标点时只开不关
    if (marker && popup && !popup.isOpen()) marker.togglePopup()
  },
)

onBeforeUnmount(() => {
  clearMarkers()
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
