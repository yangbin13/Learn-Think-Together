import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, BookOpen, ChevronRight, Eye, EyeOff, ImagePlus, Minus, Pencil, Plus, RotateCcw, Search, Trash2 } from 'lucide-react'
import { api, downloadUrl, previewUrl } from '../api'
import type { MindMapEvidence, StructureNode, TextbookFigure } from '../api'
import { cn, Empty, ErrorNotice, Modal, percentage, Spinner, useResource } from '../components/ui'
import MindMapCanvas from '../components/MindMapCanvas'
import MindMapFigure from '../components/MindMapFigure'
import FigureMarkerDialog from '../components/FigureMarkerDialog'
import { ancestors } from '../lib/knowledge-map'
import { createMindMap, descendantCount, shortTopic, topicLabel, visibleSelection, visibleTopics } from '../lib/mind-map'
import './mind-map.css'

function OriginalExcerpt({ item }: { item: MindMapEvidence['evidence'][number] }) {
  const physical = item.page_kind === 'physical'
  return <div className="mindmap-excerpt"><p>{item.excerpt}</p><a href={physical ? previewUrl(item.document_id, item.page) : downloadUrl(item.document_id)} target={physical ? '_blank' : undefined} download={!physical || undefined} rel="noopener noreferrer">{item.title} · {physical ? '物理页' : '逻辑段'} {item.page}{!physical && ' · 下载原文件'}<BookOpen size={13}/></a></div>
}

export default function Graph({ courseId, courseTitle, revision, ask }: { courseId: string; courseTitle: string; revision: number; ask: (question: string) => void }) {
  const graph = useResource(() => api.graph(courseId), [courseId, revision])
  const documents = useResource(() => api.documents(courseId), [courseId, revision])
  const [documentId, setDocumentId] = useState('all')
  const [showFigures, setShowFigures] = useState(true)
  const model = useMemo(() => graph.data ? createMindMap(graph.data, courseTitle, documentId, showFigures) : null, [graph.data, courseTitle, documentId, showFigures])
  const [focusId, setFocusId] = useState(''); const [selectedId, setSelectedId] = useState('')
  const [expanded, setExpanded] = useState(new Set<string>())
  const [recall, setRecall] = useState(false); const [revealed, setRevealed] = useState(new Set<string>())
  const [search, setSearch] = useState(''); const [searchOpen, setSearchOpen] = useState(false)
  const [evidence, setEvidence] = useState<MindMapEvidence | null>(null); const [evidenceError, setEvidenceError] = useState(''); const [evidenceLoading, setEvidenceLoading] = useState(false); const [evidenceRetry, setEvidenceRetry] = useState(0)
  const [editingNode, setEditingNode] = useState<StructureNode | null>(null)
  const [draftLabel, setDraftLabel] = useState(''); const [draftNote, setDraftNote] = useState('')
  const [saving, setSaving] = useState(false); const [saveError, setSaveError] = useState(''); const [saveNotice, setSaveNotice] = useState('')
  const [addingFigure, setAddingFigure] = useState(false); const [deletingFigure, setDeletingFigure] = useState<StructureNode | null>(null)
  const [figureBusy, setFigureBusy] = useState(''); const [figureError, setFigureError] = useState('')
  const [anchorId, setAnchorId] = useState('')
  const branchViews = useRef(new Map<string, Set<string>>())
  const details = useRef<HTMLElement>(null)
  const currentRoot = model?.index.has(focusId) ? focusId : model?.rootId ?? ''
  const selected = model?.index.get(selectedId) ?? model?.index.get(currentRoot)
  const visible = useMemo(() => model ? visibleTopics(model, currentRoot, expanded) : [], [model, currentRoot, expanded])
  const hidden = recall && selected?.id !== currentRoot && !revealed.has(selected?.id ?? '')
  const path = model && selected ? ancestors(selected.id, model.index) : []
  const focusNode = model?.index.get(currentRoot)
  const pdfDocuments = documents.data?.filter(item => item.course_id === courseId && item.status === 'ready' && item.filename.toLocaleLowerCase().endsWith('.pdf')) ?? []
  const figureJobs = graph.data?.metadata?.figure_index ?? []
  const pendingFigures = figureJobs.some(item => item.status === 'queued' || item.status === 'processing')
  const focusPath = model && focusNode ? [...ancestors(currentRoot, model.index), focusNode] : []
  const matches = useMemo(() => {
    if (!model || !search.trim()) return []
    const term = search.trim().toLocaleLowerCase()
    return [...model.index.values()].filter(node => node.kind !== 'document' && ancestors(node.id, model.index).some(parent => parent.id === model.rootId) && `${topicLabel(node)} ${node.label} ${node.heading}`.toLocaleLowerCase().includes(term))
  }, [model, search])

  useEffect(() => {
    if (!model) return
    branchViews.current.clear()
    setFocusId(model.rootId); setSelectedId(model.rootId); setAnchorId(model.rootId); setExpanded(new Set([model.rootId])); setRevealed(new Set()); setRecall(false); setSearch(''); setSearchOpen(false); setFigureError('')
  }, [model?.rootId, courseId, revision, documentId])
  useEffect(() => {
    if (!pendingFigures) return
    let active = true
    const timer = setInterval(() => { api.graph(courseId).then(value => { if (active) { graph.setData(value); setFigureError('') } }).catch(reason => { if (active) setFigureError(reason instanceof Error ? reason.message : '图示索引状态读取失败') }) }, 3000)
    return () => { active = false; clearInterval(timer) }
  }, [courseId, pendingFigures])
  useEffect(() => { setSaveNotice(''); setEditingNode(null); setSaveError('') }, [courseId, selected?.id])
  useEffect(() => {
    let active = true
    setEvidence(null); setEvidenceError(''); setEvidenceLoading(false)
    if (!selected || selected.kind === 'document' || hidden) return () => { active = false }
    setEvidenceLoading(true)
    api.mindmapEvidence(courseId, selected.id).then(value => { if (active) setEvidence(value) }).catch(error => { if (active) setEvidenceError(error instanceof Error ? error.message : '教材依据读取失败') }).finally(() => { if (active) setEvidenceLoading(false) })
    return () => { active = false }
  }, [courseId, selected?.id, hidden, evidenceRetry])
  const focus = useCallback((id: string) => {
    if (!model?.index.has(id)) return
    branchViews.current.set(currentRoot, new Set(expanded))
    setFocusId(id); setSelectedId(id); setAnchorId(id); setExpanded(new Set(branchViews.current.get(id) ?? [id])); setRevealed(new Set()); setSearch(''); setSearchOpen(false)
  }, [model, currentRoot, expanded])
  const select = useCallback((id: string) => {
    setSelectedId(id); setRevealed(value => new Set([...value, id]))
  }, [])
  const toggle = useCallback((id: string) => {
    if (!model) return
    const next = new Set(expanded); if (next.has(id)) next.delete(id); else next.add(id)
    setAnchorId(id); setExpanded(next); setSelectedId(visibleSelection(model, currentRoot, next, selectedId))
  }, [model, currentRoot, expanded, selectedId])
  const collapseAll = () => { setAnchorId(currentRoot); setExpanded(new Set()); setSelectedId(currentRoot) }
  const expandOneLevel = () => { setAnchorId(selected?.id ?? currentRoot); setExpanded(value => new Set([...value, ...visible.filter(node => !recall || node.depth === 0 || revealed.has(node.id)).map(node => node.id)])) }
  const find = (id: string) => {
    if (!model) return
    const parents = ancestors(id, model.index)
    const chapter = parents.find(node => node.kind === 'chapter')
    const localRoot = model.index.get(id)?.kind === 'figure' ? parents.at(-1)?.id ?? model.rootId : chapter?.id ?? parents.find(node => node.id !== model.rootId)?.id ?? model.rootId
    branchViews.current.set(currentRoot, new Set(expanded))
    setFocusId(localRoot); setSelectedId(id); setAnchorId(localRoot); setExpanded(new Set([...(branchViews.current.get(localRoot) ?? []), localRoot, ...parents.map(node => node.id)])); setRevealed(value => new Set([...value, id])); setSearchOpen(false)
  }
  const changeMode = () => { setRecall(value => !value); setRevealed(new Set()); setSelectedId(currentRoot); setSearch(''); setSearchOpen(false) }
  const title = selected ? selected.id === model?.rootId ? model?.rootLabel ?? courseTitle : topicLabel(selected) : ''
  const location = selected?.page_start != null ? `${selected.page_kind === 'physical' ? '物理页' : '逻辑段'} ${selected.page_start}${selected.page_end && selected.page_end !== selected.page_start ? `–${selected.page_end}` : ''}` : ''
  const requestHelp = (kind: 'explain' | 'recall' | 'figure') => {
    if (!selected) return
    const source = path.find(node => node.document_id)?.label ?? courseTitle
    const topic = `「${topicLabel(selected)}」${selected.display_label ? `（教材原提取标题：「${selected.heading}」）` : ''}`
    if (kind === 'figure') { ask(`我正在看${source}中的${topic}，文档 ID 为 ${selected.document_id}，位于原 PDF 物理页 ${selected.page_start}。请调用页图识别工具读取这一实际页面，结合图形说明它表达的知识点、图中关键标记与所属章节的联系。先确认图号和图题，不要只根据提取的图题推测图片内容；不确定的符号请说明并给出实际页图依据。`); return }
    ask(kind === 'explain' ? `我在复习${topic}。请先检索${source}的原文，说明这个知识点解决什么问题、关键概念是什么，以及它在所属章节中的位置。用一个简短例子帮助理解，并给出教材依据。` : `我正在用思维导图回忆${topic}。请结合${source}先问我一个检查理解的问题，暂时不要直接给答案；等我回答后再结合教材纠正理解，并给出依据。`)
  }
  const figureAdded = (figure: TextbookFigure) => {
    setAddingFigure(false); setFigureError(''); setShowFigures(true)
    api.graph(courseId).then(value => {
      graph.setData(value)
      const nextModel = createMindMap(value, courseTitle, documentId, true)
      if (!nextModel.index.has(figure.figure_id)) { setDocumentId('all'); return }
      const parents = ancestors(figure.figure_id, nextModel.index)
      const center = parents.at(-1)?.id ?? nextModel.rootId
      branchViews.current.set(currentRoot, new Set(expanded)); setFocusId(center); setSelectedId(figure.figure_id); setAnchorId(center)
      setExpanded(new Set([center, ...parents.map(node => node.id)])); setRevealed(new Set([figure.figure_id]))
    }).catch(reason => { setFigureError(`图示已保存，但导图刷新失败：${reason instanceof Error ? reason.message : '请刷新页面'}`) })
  }
  const removeFigure = async () => {
    if (!deletingFigure?.document_id || !deletingFigure.figure_id) return
    setFigureBusy(deletingFigure.id); setFigureError('')
    try { await api.deleteFigure(deletingFigure.document_id, deletingFigure.figure_id); setDeletingFigure(null); setSelectedId(currentRoot); graph.setData(await api.graph(courseId)) }
    catch (reason) { setFigureError(reason instanceof Error ? reason.message : '图示删除失败') }
    finally { setFigureBusy('') }
  }
  const retryFigures = async (id: string) => {
    setFigureBusy(id); setFigureError('')
    try { await api.retryFigures(id); graph.setData(await api.graph(courseId)) }
    catch (reason) { setFigureError(reason instanceof Error ? reason.message : '图示索引重试失败') }
    finally { setFigureBusy('') }
  }
  const editAnnotation = () => {
    if (!selected || selected.kind === 'document') return
    setEditingNode(selected); setDraftLabel(selected.display_label ?? ''); setDraftNote(selected.study_note ?? ''); setSaveError(''); setSaveNotice('')
  }
  const saveAnnotation = async () => {
    if (!editingNode || saving) return
    setSaving(true); setSaveError('')
    try {
      const result = await api.saveMindmapAnnotation(courseId, editingNode.id, draftLabel, draftNote)
      graph.setData(value => value ? { ...value, tree: { ...value.tree, nodes: value.tree.nodes.map(node => node.id === result.node_id ? { ...node, display_label: result.display_label, study_note: result.study_note, annotation_updated_at: result.updated_at } : node) } } : value)
      setEditingNode(null); setSaveNotice(result.updated_at ? '标题与笔记已保存到你的账号。' : '已恢复原提取标题并清空笔记。')
    } catch (error) { setSaveError(error instanceof Error ? error.message : '保存失败，请重试。') }
    finally { setSaving(false) }
  }
  const recalledCount = visible.filter(node => node.depth > 0 && revealed.has(node.id)).length

  return <div className="course-panel-content mindmap-page">
    <div className="mindmap-heading"><h2>课程思维导图</h2><p>先把课程连成框架，再逐章展开。遮住分支名称，可以检查自己是否记住了结构。</p></div>
    {graph.loading ? <Spinner label="正在整理课程导图"/> : graph.error ? <ErrorNotice error={graph.error} retry={graph.reload}/> : !graph.data?.tree.root_ids.length || !model ? <Empty title="上传教材，开始梳理课程">有可解析的章节标题后，这里会生成课程与章节分支。</Empty> : <>
      <div className="mindmap-toolbar">
        <div className="mindmap-search"><label className="search-field"><Search size={17}/><input value={search} disabled={recall} onFocus={() => setSearchOpen(true)} onChange={event => { setSearch(event.target.value); setSearchOpen(true) }} onKeyDown={event => { if (event.key === 'Escape') setSearchOpen(false) }} placeholder="查找章节、知识点或图号" aria-label="查找章节或知识点"/>{search && <button aria-label="清除导图搜索" onClick={() => { setSearch(''); setSearchOpen(false) }}>清除</button>}</label>
          {searchOpen && search.trim() && <div className="mindmap-search-results" aria-label="导图搜索结果"><p role="status">{matches.length ? `找到 ${matches.length} 个条目` : '没有匹配的章节或知识点'}</p>{matches.map(node => <button key={node.id} onClick={() => find(node.id)}><strong>{topicLabel(node)}</strong><span>{ancestors(node.id, model.index).filter(parent => parent.kind !== 'document').map(parent => shortTopic(topicLabel(parent))).join(' / ')}</span></button>)}</div>}
        </div>
        {graph.data.tree.root_ids.length > 1 && <select aria-label="选择导图教材" value={documentId} onChange={event => setDocumentId(event.target.value)}><option value="all">整门课程</option>{graph.data.tree.root_ids.map(id => <option key={id} value={id}>{graph.data!.tree.nodes.find(node => node.id === id)?.label}</option>)}</select>}
        {(graph.data.metadata?.total_figures ?? 0) > 0 && <button className="button secondary compact" aria-pressed={showFigures} onClick={() => { setShowFigures(value => !value); setSelectedId(currentRoot); setAnchorId(currentRoot) }}>{showFigures ? <Eye size={16}/> : <EyeOff size={16}/>} {showFigures ? '隐藏图示' : '显示图示'}</button>}
        <button className="button secondary compact" disabled={recall || !pdfDocuments.length} onClick={() => { setFigureError(''); setAddingFigure(true) }}><ImagePlus size={16}/>补充图示</button>
        <button className={cn('button compact', recall ? 'primary' : 'secondary')} aria-pressed={recall} onClick={changeMode}>{recall ? <Eye size={16}/> : <EyeOff size={16}/>} {recall ? '退出回忆模式' : '进入回忆模式'}</button>
        {recall && <button className="button secondary compact" onClick={() => { setRevealed(new Set()); setSelectedId(currentRoot) }}><RotateCcw size={15}/>重新遮住</button>}
      </div>
      {figureError && !deletingFigure && <ErrorNotice error={figureError}/>}
      {pendingFigures && <p className="mindmap-source-note" role="status">正在整理教材图题，完成后会出现在对应章节。原文问答可以继续使用。</p>}
      {figureJobs.filter(item => item.status === 'failed' || item.status === 'not_indexed').map(item => <div className="mindmap-index-error" key={item.document_id}><p>{item.status === 'failed' ? '这份教材的图题整理失败' : '这份教材尚未整理图题'}，可以重试。{item.error && ` ${item.error}`}</p><button className="button secondary compact" disabled={!!figureBusy} onClick={() => void retryFigures(item.document_id)}>{figureBusy === item.document_id ? '正在提交…' : '整理图题'}</button></div>)}
      <div className="mindmap-navigation"><nav aria-label="导图路径"><button onClick={() => focus(model.rootId)}>全课总览</button>{focusPath.filter(node => node.id !== model.rootId).map(node => <span key={node.id}><ChevronRight size={14}/><button onClick={() => focus(node.id)}>{shortTopic(topicLabel(node))}</button></span>)}</nav><div><button onClick={collapseAll}>收起全部</button><button onClick={expandOneLevel}>展开一级</button></div></div>
      <div className="mindmap-status" role="status"><span>{currentRoot === model.rootId ? '全课框架' : '分支复习'} · 当前显示 {visible.length} / {1 + descendantCount(currentRoot, model.index)} 个条目{(graph.data.metadata?.total_figures ?? 0) > 0 && ` · 教材图示 ${graph.data.metadata!.total_figures}`}</span><span>{recall ? `已揭示 ${recalledCount} / ${Math.max(0, visible.length - 1)} 个分支；先回忆，再点击核对` : '连线表示教材层级；展开分支保持缩放，可随时收起'}</span></div>
      {selected && !hidden && <div className="mindmap-selection"><strong>已选：{title}</strong>{selected.children.length > 0 && <button aria-expanded={expanded.has(selected.id)} onClick={() => toggle(selected.id)}>{expanded.has(selected.id) ? <Minus size={14}/> : <Plus size={14}/>} {expanded.has(selected.id) ? '收起此分支' : '展开此分支'}</button>}{selected.children.length > 0 && selected.id !== currentRoot && <button onClick={() => focus(selected.id)}>{selected.kind === 'chapter' ? '进入本章' : '以此为中心'}<ArrowRight size={14}/></button>}<button onClick={() => details.current?.scrollIntoView({ block: 'start' })}>查看原文与图示<BookOpen size={14}/></button></div>}
      <MindMapCanvas model={model} focusId={currentRoot} expanded={expanded} selectedId={selected?.id ?? ''} recall={recall} revealed={revealed} select={select} toggle={toggle} focus={focus} anchorId={anchorId || currentRoot} showDetails={() => details.current?.scrollIntoView({ block: 'start' })}/>
      <section ref={details} className="mindmap-details" aria-label="知识点学习详情">
        {selected && !hidden ? <>
          <div className="mindmap-detail-heading"><div><h3>{title}</h3>{path.some(node => node.kind !== 'document') && <p className="mindmap-detail-path">{path.filter(node => node.kind !== 'document').map(node => topicLabel(node)).join(' / ')}</p>}</div><span className="knowledge-mastery">{selected.kind === 'figure' ? selected.origin === 'manual' ? '个人图示' : '教材图示' : selected.mastery == null ? '尚未测评' : `题目掌握度 ${percentage(selected.mastery)}%`}</span></div>
          {selected.display_label && <p className="mindmap-source-note mindmap-corrected-title">你的标题校正 · 原提取标题：{selected.label}</p>}
          {selected.kind === 'document' ? <p className="mindmap-detail-intro">选择一个章节，先看它的分支结构，再展开到需要理解的知识点。学完可以开启回忆模式核对框架。</p> : selected.kind === 'figure' ? <p className="mindmap-detail-intro">先对照原图回想：每个标记是什么意思？图中的关系、过程或变化，如何帮助理解这一节？</p> : <p className="mindmap-detail-intro">先试着回答：这个知识点解决什么问题？它和所属章节的其他内容有什么联系？</p>}
          <div className="mindmap-actions">{selected.children.length > 0 && selected.id !== currentRoot && <button className="button primary compact" onClick={() => focus(selected.id)}>{selected.kind === 'chapter' ? '进入本章' : '以此为中心'}<ArrowRight size={16}/></button>}{currentRoot !== model.rootId && <button className="button secondary compact" onClick={() => focus(model.rootId)}><ArrowLeft size={16}/>返回全课</button>}{selected.kind !== 'document' && <><button className="button secondary compact" onClick={() => requestHelp(selected.kind === 'figure' ? 'figure' : 'explain')}>{selected.kind === 'figure' ? '让助教看这张图' : '让助教讲解'}</button><button className="button secondary compact" onClick={() => requestHelp('recall')}>让助教考考我</button></>}{selected.document_id && <a className="button secondary compact" href={selected.page_kind === 'physical' ? previewUrl(selected.document_id, selected.page_start) : downloadUrl(selected.document_id)} target={selected.page_kind === 'physical' ? '_blank' : undefined} download={selected.page_kind !== 'physical' || undefined} rel="noopener noreferrer"><BookOpen size={16}/>{selected.page_kind === 'physical' ? location || '打开教材' : ['下载原文件', location].filter(Boolean).join(' · ')}</a>}{selected.kind === 'figure' && selected.origin === 'manual' && <button className="button secondary compact" onClick={() => { setFigureError(''); setDeletingFigure(selected) }}><Trash2 size={15}/>删除个人标注</button>}</div>
          {selected.kind !== 'document' && <div className="mindmap-personal-note"><div><h4>我的理解</h4><button onClick={editAnnotation}><Pencil size={14}/>编辑标题与笔记</button></div>{selected.study_note ? <p>{selected.study_note}</p> : <p className="mindmap-note-empty">用自己的话记下它解决的问题，或补充容易混淆的概念。</p>}{saveNotice && <p className="mindmap-save-notice" role="status">{saveNotice}</p>}</div>}
          {selected.kind !== 'document' && <div className={cn('mindmap-evidence', selected.kind === 'figure' && 'mindmap-figure-evidence')}><h4>{selected.kind === 'figure' ? '教材原页图' : '教材原文'}</h4>{evidenceLoading ? <Spinner label="正在读取教材依据"/> : evidenceError ? <ErrorNotice error={evidenceError} retry={() => setEvidenceRetry(value => value + 1)}/> : evidence?.node_id === selected.id ? <>{evidence.figure && <MindMapFigure key={selected.id} figure={evidence.figure}/>}{evidence.evidence.slice(0, 1).map(item => <OriginalExcerpt key={item.chunk_id} item={item}/>)}{evidence.evidence.length > 1 && <details className="mindmap-more-evidence" key={selected.id}><summary>展开另外 {evidence.evidence.length - 1} 段原文</summary>{evidence.evidence.slice(1).map(item => <OriginalExcerpt key={item.chunk_id} item={item}/>)}</details>}{!evidence.figure && <p className="mindmap-source-note">{evidence.notice}</p>}</> : null}</div>}
          {selected.kind !== 'figure' && <p className="mindmap-source-note">{selected.source}{selected.kind === 'document' ? '；分支来自教材标题，仍需结合原文核对。' : ''}</p>}
        </> : <p className="mindmap-detail-intro">先根据图中的位置回想这个分支，再点击节点揭示名称。名称尚未揭示时，不提前显示原文答案。</p>}
      </section>
    </>}
    {addingFigure && <FigureMarkerDialog documents={pdfDocuments} initialDocument={selected?.document_id} initialPage={selected?.page_start} close={() => setAddingFigure(false)} added={figureAdded}/>}
    {deletingFigure && <Modal title="删除这条个人图示标注？" onClose={() => { if (!figureBusy) setDeletingFigure(null) }}><div className="mindmap-note-form"><p>将删除「{topicLabel(deletingFigure)}」及这条标注的个人笔记。教材原页和其他学习记录仍保留。</p>{figureError && <ErrorNotice error={figureError}/>}<div className="mindmap-note-form-actions"><button className="button secondary compact" disabled={!!figureBusy} onClick={() => setDeletingFigure(null)}>保留标注</button><button className="button danger compact" disabled={!!figureBusy} onClick={() => void removeFigure()}>{figureBusy ? '正在删除…' : '确认删除标注'}</button></div></div></Modal>}
    {editingNode && <Modal title="编辑标题与学习笔记" onClose={() => { if (!saving) setEditingNode(null) }}><form className="mindmap-note-form" onSubmit={event => { event.preventDefault(); void saveAnnotation() }}><p>先核对教材，再校正目录标题。修改只保存在你的账号中，教材原文与定位保持原样。</p><label>显示标题<input aria-label="显示标题" data-dialog-autofocus value={draftLabel} disabled={saving} maxLength={160} placeholder={editingNode.label} onChange={event => setDraftLabel(event.target.value)}/><span>留空使用原提取标题：{editingNode.label}</span></label><label>学习笔记<textarea aria-label="学习笔记" value={draftNote} disabled={saving} maxLength={2000} rows={5} placeholder="试着用自己的话解释这个知识点。" onChange={event => setDraftNote(event.target.value)}/><span>{draftNote.length} / 2000 字</span></label>{saveError && <ErrorNotice error={saveError}/>}<button type="button" className="mindmap-clear-note" disabled={saving} onClick={() => { setDraftLabel(''); setDraftNote('') }}>恢复原标题并清空笔记</button><div className="mindmap-note-form-actions"><button type="button" className="button secondary compact" disabled={saving} onClick={() => setEditingNode(null)}>取消</button><button type="submit" className="button primary compact" disabled={saving}>{saving ? '正在保存…' : '保存'}</button></div></form></Modal>}
  </div>
}
