import { AlertTriangle, CheckCircle2, ChevronDown, Database, FileSearch, LoaderCircle, Network } from 'lucide-react'
import type { IngestionProgress, Material } from '../api'
import { cn } from './ui'
const stages: { id: IngestionProgress['stage']; title: string }[] = [{ id: 'extracting', title: '提取原文' }, { id: 'chunking', title: '整理片段' }, { id: 'embedding', title: '建立语义索引' }, { id: 'persisting', title: '保存教材' }]
export const materialPageUnit = (document: Material) => document.quality?.page_kind === 'physical' || document.source_type.endsWith('_pdf') || document.source_type === 'open_textbook' ? '物理页' : '逻辑段'
export function MaterialProgress({ material, compact = false }: { material: Material; compact?: boolean }) {
  const progress = material.progress || {}; const unit = materialPageUnit(material); const pending = material.status === 'queued' || material.status === 'processing'; const stage = material.status === 'queued' ? 'queued' : material.status === 'failed' ? 'failed' : progress.stage || (material.status === 'ready' ? 'ready' : undefined)
  const stageIndex = stages.findIndex(item => item.id === stage); const totalPages = progress.total_pages || material.page_count; const current = progress.current_page; const chunks = progress.chunk_count || material.chunk_count; const embedding = material.embedding_status || material.quality?.semantic_status
  const label = stage === 'queued' ? '排队等待解析' : stage === 'ready' ? '教材解析完成' : stage === 'failed' ? '这次解析未能完成' : stages.find(item => item.id === stage)?.title || '正在处理教材'
  const measured = stage === 'embedding' ? progress.embedding_percent : (stage === 'extracting' || stage === 'chunking') && totalPages > 0 && current != null ? current / totalPages * 100 : undefined
  const percent = measured == null ? null : Math.max(0, Math.min(100, Math.round(measured * 10) / 10))
  return <div className={cn('ingestion-status', compact && 'ingestion-compact')}>
    {pending && <><div className="ingestion-stage-label"><LoaderCircle size={12} className="spin"/><strong>{label}</strong>{percent != null && <span>{percent}%</span>}</div>{!compact && <div className="ingestion-steps" aria-label="教材解析阶段">{stages.map((item, index) => <span key={item.id} className={cn(index === stageIndex && 'current', index < stageIndex && (item.id !== 'embedding' || progress.embedding_completed === chunks && chunks > 0) && 'complete')}><i>{index < stageIndex && (item.id !== 'embedding' || progress.embedding_completed === chunks && chunks > 0) ? <CheckCircle2 size={11}/> : index + 1}</i>{item.title}</span>)}</div>}{percent != null && <div className="ingestion-meter" role="progressbar" aria-label={`${label}进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><i style={{ width: `${percent}%` }}/></div>}</>}
    <div className="ingestion-facts">{totalPages > 0 && <span><FileSearch size={11}/>{pending && (stage === 'extracting' || stage === 'chunking') && current != null ? `${current.toLocaleString()} / ${totalPages.toLocaleString()} ${unit}` : `${totalPages.toLocaleString()} ${unit}`}</span>}{chunks > 0 && <span><Database size={11}/>{chunks.toLocaleString()} 个片段</span>}{(stage === 'embedding' || embedding === 'hybrid' || embedding === 'lexical_fallback') && progress.embedding_completed != null && chunks > 0 && <span><Network size={11}/>{progress.embedding_completed.toLocaleString()} / {chunks.toLocaleString()} 已建立向量{progress.embedding_percent != null && ` · ${progress.embedding_percent}%`}</span>}{material.status === 'ready' && <span className={cn('index-state', embedding === 'lexical_fallback' && 'fallback')}><Network size={11}/>{embedding === 'hybrid' ? '关键词 + 语义检索' : embedding === 'lexical_fallback' ? '语义暂不可用 · 关键词检索' : '本地关键词检索'}</span>}</div>
    {pending && !compact && <p className="ingestion-persistence-note">大部头教材需要逐页处理，进度会自动更新。离开页面后可回来查看，无需重复上传。</p>}
    {material.status === 'failed' && material.error && <p className="ingestion-error" role="alert"><AlertTriangle size={13}/>{material.error}</p>}
    {material.status === 'ready' && embedding === 'lexical_fallback' && <p className="ingestion-fallback"><AlertTriangle size={13}/><span>原文已可检索，语义索引未完成。{!compact && (material.quality?.semantic_error || '可重新尝试建立语义索引。')}</span></p>}
    {compact && !!material.quality?.warnings?.length && <p className="ingestion-compact-warning"><AlertTriangle size={11}/>{material.quality.warnings.length} 项解析提示，点击教材查看</p>}
  </div>
}
export function MaterialQualityView({ material, expanded = false }: { material: Material; expanded?: boolean }) {
  const quality = material.quality; if (!quality || !Object.keys(quality).length) return null
  const warnings = quality.warnings || []; const unit = materialPageUnit(material); const hasMetrics = quality.total_pages != null || quality.text_pages != null || quality.character_count != null
  return <details className={cn('material-quality', warnings.length > 0 && 'has-warnings')} open={expanded || undefined}>
    <summary><FileSearch size={13}/>教材解析报告{warnings.length > 0 && <span>{warnings.length} 项提示</span>}<ChevronDown size={13}/></summary><div className="quality-content">
      {hasMetrics && <div className="quality-metrics">{[{ label: `总${unit}数`, value: quality.total_pages }, { label: `有文本${unit}`, value: quality.text_pages }, { label: `无文本${unit}`, value: quality.blank_pages }, { label: `文字较少${unit}`, value: quality.short_pages }, { label: '提取字符数', value: quality.character_count }, { label: '规范化字符数', value: quality.normalized_characters }].filter(item => item.value != null).map(item => <span key={item.label}><small>{item.label}</small><strong>{item.value?.toLocaleString()}</strong></span>)}</div>}
      {quality.chapter_source && <p className="quality-chapters"><strong>章节定位</strong>{quality.chapter_source}{quality.outline_entries != null && ` · ${quality.outline_entries} 条目录项`}</p>}
      {warnings.length > 0 && <ul className="quality-warnings">{warnings.map((warning, index) => <li key={`${index}-${warning}`}><AlertTriangle size={12}/>{warning}</li>)}</ul>}
      {quality.semantic_error && <p className="quality-semantic-error"><strong>语义索引提示</strong>{quality.semantic_error}</p>}{quality.semantic_note && <p className="quality-semantic-note">{quality.semantic_note}</p>}
      <p className="quality-boundary">解析报告描述提取到的文字与索引状态。图表、扫描图片和复杂公式可能无法完整还原，学习时仍需核对原始教材。</p>
    </div>
  </details>
}
