import { useState } from 'react'
import type { Material, TextbookFigure } from '../api'
import { api, previewUrl } from '../api'
import { ErrorNotice, Modal } from './ui'

export default function FigureMarkerDialog({ documents, initialDocument, initialPage, close, added }: { documents: Material[]; initialDocument?: string | null; initialPage?: number | null; close: () => void; added: (figure: TextbookFigure) => void }) {
  const [documentId, setDocumentId] = useState(documents.some(item => item.id === initialDocument) ? initialDocument! : documents[0]?.id ?? '')
  const [page, setPage] = useState(String(initialPage ?? 1))
  const [number, setNumber] = useState(''); const [caption, setCaption] = useState('')
  const [saving, setSaving] = useState(false); const [error, setError] = useState('')
  const document = documents.find(item => item.id === documentId)
  const pageNumber = Number(page)
  const validPage = /^\d+$/.test(page) && Number.isSafeInteger(pageNumber) && pageNumber > 0 && !!document && pageNumber <= document.page_count
  const submit = async () => {
    if (!validPage || !caption.trim() || saving) return
    setSaving(true); setError('')
    try { const figure = await api.addFigure(documentId, pageNumber, number.trim(), caption.trim()); added(figure) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '标注保存失败，请重试。'); setSaving(false) }
  }
  return <Modal title="补充一张教材图示" onClose={() => { if (!saving) close() }}><form className="mindmap-note-form" onSubmit={event => { event.preventDefault(); void submit() }}><p>文字识别漏掉的图示可以手动补充。先在 PDF 中核对图号和物理页，标注将挂到对应章节，仅你可见。</p><label>选择教材<select aria-label="标注的教材" disabled={saving} value={documentId} onChange={event => { setDocumentId(event.target.value); setPage('1') }}>{documents.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label><label>物理页<input aria-label="图示物理页" data-dialog-autofocus type="number" min={1} max={document?.page_count ?? 1} step={1} value={page} disabled={saving} onChange={event => setPage(event.target.value)}/><span>PDF 阅读器的页序号，范围 1–{document?.page_count ?? 0}；不是书内印刷页码。{validPage && <a href={previewUrl(documentId, pageNumber)} target="_blank" rel="noopener noreferrer">打开此页核对</a>}</span></label><label>图号（选填）<input aria-label="图示编号" value={number} maxLength={40} placeholder="例如 3-52" disabled={saving} onChange={event => setNumber(event.target.value)}/></label><label>图示标题<input aria-label="图示标题" value={caption} maxLength={200} placeholder="照原图填写，或简要说明想记录的内容" disabled={saving} onChange={event => setCaption(event.target.value)}/></label>{error && <ErrorNotice error={error}/>}<div className="mindmap-note-form-actions"><button className="button secondary compact" type="button" disabled={saving} onClick={close}>取消</button><button className="button primary compact" type="submit" disabled={!validPage || !caption.trim() || saving}>{saving ? '正在保存…' : '保存图示标注'}</button></div></form></Modal>
}
