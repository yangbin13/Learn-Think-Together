import { useState } from 'react'
import { ArrowRight, BookOpen, Cpu, Globe, Layers, LoaderCircle, Network, Plus, Search, Trash2 } from 'lucide-react'
import { api } from '../api'
import type { Course, User } from '../api'
import { cn, Empty, ErrorNotice, Modal, Spinner } from '../components/ui'

const icons = { network: Network, cpu: Cpu, layers: Layers, globe: Globe }
export function CourseIcon({ course, size = 23 }: { course: Course; size?: number }) {
  const Icon = icons[course.icon] || BookOpen
  return <Icon size={size} strokeWidth={1.8}/>
}

export default function Library({ user, courses, loading, refresh, openCourse }: {
  user: User; courses: Course[]; loading: boolean; refresh: () => Promise<void>; openCourse: (id: string) => void
}) {
  const [search, setSearch] = useState(''); const [filter, setFilter] = useState('all')
  const [adding, setAdding] = useState(false); const [title, setTitle] = useState(''); const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [deleting, setDeleting] = useState<Course | null>(null)
  const filtered = courses.filter(course => (filter !== 'custom' || !course.is_builtin) && (filter !== 'builtin' || course.is_builtin)
    && (course.title + course.description).toLowerCase().includes(search.trim().toLowerCase()))
  const add = async () => {
    setBusy(true); setError('')
    try {
      const course = await api.createCourse(title.trim(), description.trim())
      await refresh(); setAdding(false); setTitle(''); setDescription(''); openCourse(course.id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '创建失败') }
    finally { setBusy(false) }
  }
  const remove = async () => {
    if (!deleting) return
    setBusy(true); setError('')
    try { await api.deleteCourse(deleting.id); await refresh(); setDeleting(null) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '删除失败') }
    finally { setBusy(false) }
  }
  return <>
    <div className="library-greeting">
      <div><h1>我的课程</h1><p>{user.display_name}，选择课程继续学习，或上传教材创建新课程。</p></div>
      <button className="button primary" onClick={() => { setError(''); setAdding(true) }}><Plus size={18}/>创建我的课程</button>
    </div>
    <section className="bookshelf" aria-label="课程列表">
      <div className="bookshelf-toolbar">
        <div className="filter-tabs" role="group" aria-label="课程筛选">
          {[['all', '全部课程'], ['builtin', '408 核心课'], ['custom', '我的专业课']].map(([key, label]) =>
            <button className={cn(filter === key && 'active')} aria-pressed={filter === key} key={key} onClick={() => setFilter(key)}>{label}</button>)}
        </div>
        <label className="search-field"><Search size={18}/><input aria-label="搜索课程" placeholder="搜索课程" value={search} onChange={event => setSearch(event.target.value)}/></label>
      </div>
      <p className="course-list-count">{loading ? '正在加载课程' : `共 ${filtered.length} 门课程`}<span>每门课程都有独立教材与助教对话。</span></p>
      {loading && !courses.length ? <Spinner label="正在加载课程"/> : <div className="course-grid">
        {filtered.map(course => <article className="course-card" key={course.id}>
          <div className="course-row-icon" style={{ color: course.color }}><CourseIcon course={course} size={25}/></div>
          <div className="course-card-body">
            <div className="course-card-title"><h2><button className="course-cover" onClick={() => openCourse(course.id)} aria-label={`学习${course.title}`}>{course.title}</button></h2><span className={cn('course-badge', !course.is_builtin && 'custom')}>{course.is_builtin ? '408 核心课' : '我的专业课'}</span></div>
            <p>{course.description}</p>
            <div className="course-card-meta"><span>{course.document_count} 份教材</span><span>{course.concept_count} 个概念</span></div>
          </div>
          <div className="course-card-footer">
            <button className="button secondary" onClick={() => openCourse(course.id)} aria-label={`进入${course.title}学习空间`}>进入助教<ArrowRight size={16}/></button>
            {!course.is_builtin && <button className="icon-button course-delete" aria-label={`删除${course.title}`} onClick={() => { setError(''); setDeleting(course) }}><Trash2 size={17}/></button>}
          </div>
        </article>)}
      </div>}
      {!filtered.length && !loading && <Empty title={search ? '没有找到匹配的课程' : '暂无课程'} action={search ? <button className="button secondary" onClick={() => setSearch('')}>清除搜索</button> : undefined}>{search ? '换一个关键词，或清除搜索。' : '创建课程并上传教材，即可与对应助教学习。'}</Empty>}
    </section>
    <p className="library-bottom-note">408 内置课程包含开放授权的全文教材与原创导学手册。自己的教材请上传到自建课程。</p>
    {adding && <Modal title="创建你的专属课程" onClose={() => !busy && setAdding(false)}>
      <form className="modal-form" onSubmit={event => { event.preventDefault(); void add() }}>
        <p>填写课程信息，创建后可以上传教材。</p>
        <label>课程名称<input data-dialog-autofocus value={title} onChange={event => setTitle(event.target.value)} placeholder="例如：数字信号处理" required maxLength={60}/></label>
        <label>学习目标或课程简介<textarea value={description} onChange={event => setDescription(event.target.value)} placeholder="希望理解核心概念，并准备期末考试。" rows={3} maxLength={500}/></label>
        {error && <ErrorNotice error={error}/>}
        <div className="modal-actions"><button type="button" className="button secondary" disabled={busy} onClick={() => setAdding(false)}>取消</button><button className="button primary" disabled={busy || !title.trim()}>{busy ? <LoaderCircle size={17} className="spin"/> : <Plus size={17}/>}创建课程</button></div>
      </form>
    </Modal>}
    {deleting && <Modal title="删除这门课程？" onClose={() => !busy && setDeleting(null)}>
      <div className="modal-form"><p>「{deleting.title}」及其中的教材、对话和学习记录将被删除。此操作无法撤销。</p>{error && <ErrorNotice error={error}/>}<div className="modal-actions"><button className="button secondary" disabled={busy} onClick={() => setDeleting(null)}>保留课程</button><button className="button danger" disabled={busy} onClick={() => void remove()}>{busy && <LoaderCircle size={17} className="spin"/>}确认删除</button></div></div>
    </Modal>}
  </>
}
