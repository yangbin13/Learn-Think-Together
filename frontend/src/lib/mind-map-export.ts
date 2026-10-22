import type { MindMapLayout, MindMapModel } from './mind-map'
import { mindMapSvg } from './mind-map'

// Bound the bitmap allocation on phones; never shrink labels below their
// original size just to squeeze a whole textbook into an unreadable image.
const MAX_SIDE = 8192
const MAX_PIXELS = 16_000_000

export async function mindMapPng(model: MindMapModel, layout: MindMapLayout, recall = false, revealed = new Set<string>()) {
  const svg = mindMapSvg(model, layout, recall, revealed)
  const root = new DOMParser().parseFromString(svg, 'image/svg+xml').documentElement
  const width = Number(root.getAttribute('width'))
  const height = Number(root.getAttribute('height'))
  const scale = Math.min(2, MAX_SIDE / width, MAX_SIDE / height, Math.sqrt(MAX_PIXELS / (width * height)))
  if (!Number.isFinite(scale) || scale < 1) {
    throw new Error('当前展开的内容太多，请先进入某一章或收起部分分支，再下载清晰图片。')
  }

  await document.fonts.ready
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }))
  const canvas = document.createElement('canvas')
  try {
    const image = new Image()
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve()
      image.onerror = () => reject(new Error('导图图片生成失败，请重试。'))
      image.src = url
    })
    canvas.width = Math.floor(width * scale)
    canvas.height = Math.floor(height * scale)
    const context = canvas.getContext('2d')
    if (!context) throw new Error('当前浏览器无法生成图片，请换用系统浏览器后重试。')
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('导图图片保存失败，请收起部分分支后重试。')), 'image/png')
    })
  } finally {
    URL.revokeObjectURL(url)
    canvas.width = canvas.height = 0
  }
}

export function downloadMindMap(blob: Blob, title: string, extension: 'png' | 'svg', recall = false) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${title.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')}-${recall ? '回忆导图' : '思维导图'}.${extension}`
  document.body.append(link)
  link.click()
  link.remove()
  // Leave time for the browser's download handler, including mobile browsers.
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
