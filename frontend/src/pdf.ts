/** 行程 PDF 导出：隐藏 iframe 打印方案（零依赖、中文渲染完美、文字为矢量）。
 *  在当前页挂一个屏外 iframe 写入 A4 友好 HTML，图片就绪后经 iframe 调 print()
 *  让用户"另存为 PDF"，打印结束后移除 iframe。不开新窗口，无 about:blank 标签页、
 *  也不受弹窗拦截影响。 */

import { crossDayLeg, dayTransitMinutes, formatMinutes, legBetween, legLabel, narrationOf } from './itinerary'
import { dayColor, type Itinerary } from './types'
import logoUrl from './assets/logo.png'

/** 动态文本一律 HTML 转义后再拼接，防注入/断标签。 */
function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c,
  )
}

/** 生成打印用 HTML（纸质风：白底、黑灰文字、细分割线）。 */
function buildHtml(itinerary: Itinerary): string {
  const subtitle = [itinerary.work, itinerary.area].filter(Boolean).join(' · ')

  const daysHtml = itinerary.days
    .map((day) => {
      const color = dayColor(day.day)
      const stops = day.seichi
        .map((s, i) => {
          const narration = narrationOf(day, s.id)?.text
          const leg = legBetween(day, i)
          const legRow = leg ? `<div class="leg">${escapeHtml(legLabel(leg))}</div>` : ''
          return `
            <div class="stop">
              <div class="stop-body">
                <div class="stop-name">${escapeHtml(s.name)}</div>
                ${s.image ? `<img class="stop-img" src="${escapeHtml(s.image)}" alt="${escapeHtml(s.name)}" />` : ''}
                ${narration ? `<div class="narration">${escapeHtml(narration)}</div>` : ''}
              </div>
            </div>
            ${legRow}`
        })
        .join('')
      const cross = crossDayLeg(day)
      const crossRow = cross ? `<div class="leg cross">${escapeHtml(legLabel(cross))}（次日衔接）</div>` : ''
      const transit = dayTransitMinutes(day)
      return `
        <section class="day">
          <h2 style="border-left-color: ${color}">
            <span class="day-dot" style="background: ${color}"></span>Day ${day.day} · ${day.seichi.length} 站${transit ? ` · ${formatMinutes(transit)}` : ''}
          </h2>
          ${stops}
          ${crossRow}
        </section>`
    })
    .join('')

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<title>Meguri 圣地巡礼 · 行程单</title>
<style>
  @page { size: A4; margin: 16mm 14mm; }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 24px 28px;
    font-family: 'Noto Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
    color: #1f1f19; /* 濃墨 */
    background: #fff;
    font-size: 12px;
    line-height: 1.6;
  }
  .header {
    display: flex;
    align-items: center;
    gap: 14px;
    padding-bottom: 14px;
    border-bottom: 1px solid #e9e4d4;
  }
  .header img { width: 64px; height: 64px; border-radius: 50%; object-fit: cover; }
  .header h1 {
    margin: 0;
    font-family: 'Hiragino Mincho ProN', 'Hiragino Mincho Pro', 'Yu Mincho', 'Noto Serif SC', 'Songti SC', serif; /* 与站点 --font-serif 一致 */
    font-size: 20px;
    letter-spacing: 0.06em;
  }
  .header p { margin: 4px 0 0; font-size: 12px; color: #595857; }
  .day { margin-top: 18px; }
  .day h2 {
    margin: 0 0 8px;
    font-size: 14px;
    padding-left: 8px;
    border-left: 3px solid;
    display: flex;
    align-items: center;
    gap: 6px;
    page-break-after: avoid; /* 标题不孤立在页尾，但各天内容连续排版 */
  }
  .day-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .stop { padding: 6px 0; border-bottom: 1px dashed #ede9da; page-break-inside: avoid; }
  .stop-name { font-weight: 600; }
  .stop-img { display: block; max-width: 240px; max-height: 150px; object-fit: cover; border-radius: 6px; margin-top: 4px; }
  .narration { color: #595857; font-size: 11px; margin-top: 2px; }
  .leg { padding: 2px 0 2px 10px; font-size: 10.5px; color: #a3a3a2; }
  .footer {
    margin-top: 24px;
    padding-top: 10px;
    border-top: 1px solid #e9e4d4;
    text-align: center;
    font-size: 10px;
    color: #a3a3a2;
  }
</style>
</head>
<body>
  <header class="header">
    <img id="logo" src="${logoUrl}" alt="Meguri" />
    <div>
      <h1>Meguri 圣地巡礼</h1>
      ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ''}
    </div>
  </header>
  ${daysHtml}
  <footer class="footer">由 Meguri 生成</footer>
</body>
</html>`
}

/** 把当前行程导出为 PDF（含 Meguri logo）。 */
export async function exportItineraryPdf(itinerary: Itinerary): Promise<void> {
  // 屏外 iframe：打印当前页内的文档，不开新窗口（避免 about:blank 标签页与弹窗拦截）
  const iframe = document.createElement('iframe')
  iframe.style.position = 'fixed'
  iframe.style.right = '100%'
  iframe.style.bottom = '100%'
  iframe.style.width = '0'
  iframe.style.height = '0'
  iframe.style.border = '0'
  document.body.appendChild(iframe)

  const win = iframe.contentWindow
  if (!win) {
    iframe.remove()
    throw new Error('无法创建打印环境')
  }
  win.document.open()
  win.document.write(buildHtml(itinerary))
  win.document.close()

  // 等全部图片（logo + 各站参考图）就绪再唤起打印对话框；单图挂起有超时兜底，不阻塞导出
  await new Promise<void>((resolve) => {
    const pending = Array.from(win.document.images).filter((img) => !img.complete)
    if (pending.length === 0) {
      resolve()
      return
    }
    let left = pending.length
    const done = () => {
      if (--left <= 0) resolve()
    }
    setTimeout(resolve, 8000) // 8 秒兜底：远程图挂起也照常打印（缺图好过不出纸）
    for (const img of pending) {
      img.addEventListener('load', done, { once: true })
      img.addEventListener('error', done, { once: true })
    }
  })
  win.onafterprint = () => iframe.remove()
  win.focus()
  win.print()
}
