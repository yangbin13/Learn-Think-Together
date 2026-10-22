import { useRef, useState } from 'react'
import { BookOpen, Maximize2, Minus, Plus, RefreshCw } from 'lucide-react'
import type { TextbookFigure } from '../api'
import { pageImageUrl, previewUrl } from '../api'
import { ErrorNotice, Modal } from './ui'

export default function MindMapFigure({ figure }: { figure: TextbookFigure }) {
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [large, setLarge] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [ratio, setRatio] = useState(.67)
  const scroll = useRef<HTMLDivElement>(null)
  const zoomPage = (next: number) => {
    const el = scroll.current
    const center = el ? { x: el.scrollLeft + el.clientWidth / 2, y: el.scrollTop + el.clientHeight / 2 } : null
    const scale = next / zoom
    setZoom(next)
    requestAnimationFrame(() => {
      if (!scroll.current || !center) return
      scroll.current.scrollLeft = center.x * scale - scroll.current.clientWidth / 2
      scroll.current.scrollTop = center.y * scale - scroll.current.clientHeight / 2
    })
  }
  const url = `${pageImageUrl(figure.document_id, figure.caption_page)}${attempt ? `?retry=${attempt}` : ''}`
  const alt = `${figure.figure_number ? `图 ${figure.figure_number} · ` : ''}${figure.caption}所在的教材原页，物理页 ${figure.caption_page}`
  return <div className="mindmap-figure">
    <div className="mindmap-figure-heading"><span>{figure.origin === 'manual' ? '你的图示标注' : '教材图题定位'} · 物理页 {figure.caption_page}</span><button className="button secondary compact" onClick={() => { setZoom(1); setLarge(true) }}><Maximize2 size={15}/>放大查看原页</button></div>
    {failed ? <div className="mindmap-image-error"><ErrorNotice error="原页暂时无法打开。可以重试，或打开原 PDF 核对。"/><button className="button secondary compact" onClick={() => { setFailed(false); setAttempt(value => value + 1) }}><RefreshCw size={15}/>重试原页</button></div> : <button className="mindmap-figure-preview" aria-label="放大教材原页" onClick={() => { setZoom(1); setLarge(true) }}><img src={url} alt={alt} loading="lazy" onError={() => setFailed(true)}/></button>}
    <p className="mindmap-source-note">显示完整原页；同页可能有多张图，请按图号核对。图题和位置{figure.origin === 'manual' ? '由你补充' : '由文字层提取，可能有 OCR 错字或漏项'}，图片内容需对照原页理解。</p>
    <a className="mindmap-pdf-link" href={previewUrl(figure.document_id, figure.caption_page)} target="_blank" rel="noopener noreferrer"><BookOpen size={14}/>打开原 PDF · 物理页 {figure.caption_page}</a>
    {large && <Modal title={figure.figure_number ? `图 ${figure.figure_number} · 教材原页` : '教材图示原页'} wide onClose={() => setLarge(false)}><div className="mindmap-image-viewer"><p>{figure.caption} · 物理页 {figure.caption_page}</p><div className="mindmap-image-toolbar"><button className="button secondary compact" aria-label="缩小教材原页" disabled={zoom <= 1} onClick={() => zoomPage(Math.max(1, zoom - .5))}><Minus size={15}/></button><span aria-live="polite">{Math.round(zoom * 100)}%</span><button className="button secondary compact" aria-label="放大教材原页" disabled={zoom >= 3} onClick={() => zoomPage(Math.min(3, zoom + .5))}><Plus size={15}/></button><button className="button secondary compact" onClick={() => { setZoom(1); if (scroll.current) { scroll.current.scrollTop = 0; scroll.current.scrollLeft = 0 } }}>整页适配</button><a href={previewUrl(figure.document_id, figure.caption_page)} target="_blank" rel="noopener noreferrer">在 PDF 中查看</a></div><div className="mindmap-image-scroll" ref={scroll}>{failed ? <ErrorNotice error="原页无法打开，请关闭后重试或通过 PDF 查看。"/> : <img src={url} alt={alt} style={{ width: `min(${zoom * 100}%, calc(${64 * ratio * zoom}vh - ${2 * ratio * zoom}px))` }} onLoad={event => { const image = event.currentTarget; setRatio(image.naturalWidth / image.naturalHeight) }} onError={() => setFailed(true)}/>}</div></div></Modal>}
  </div>
}
