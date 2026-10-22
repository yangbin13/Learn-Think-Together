import { useState } from 'react'
import { CalendarCheck2, Check, ChevronDown, Clock3, LoaderCircle, Plus } from 'lucide-react'
import { api } from '../api'
import type { Plan } from '../api'
import { cn, date, Empty, ErrorNotice, Modal, Spinner, useResource } from '../components/ui'

export default function Plans({ courseId, onChange }: { courseId: string; onChange: () => void }) {
  const plans = useResource(() => api.plans(courseId), [courseId])
  const [selected, setSelected] = useState<string | null>(null); const [showCreate, setShowCreate] = useState(false)
  const [days, setDays] = useState(7); const [minutes, setMinutes] = useState(30); const [goal, setGoal] = useState('')
  const [busy, setBusy] = useState(false); const [updating, setUpdating] = useState(''); const [error, setError] = useState(''); const [dayFilter, setDayFilter] = useState('all')
  const plan: Plan | undefined = plans.data?.find(item => item.id === selected) || plans.data?.[0]
  const create = async () => {
    setBusy(true); setError('')
    try { const next = await api.createPlan(courseId, days, minutes, goal.trim()); plans.setData(previous => [next, ...(previous || [])]); setSelected(next.id); setShowCreate(false); setDayFilter('all'); onChange() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '生成计划失败') } finally { setBusy(false) }
  }
  const toggle = async (itemId: string, completed: boolean) => {
    if (!plan) return
    setUpdating(itemId); setError('')
    try { const next = await api.togglePlan(plan.id, itemId, completed); plans.setData(previous => previous?.map(item => item.id === next.id ? next : item) || [next]); onChange() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '更新计划失败') } finally { setUpdating('') }
  }
  const completed = plan?.items.filter(item => item.completed).length || 0
  const progress = plan?.items.length ? Math.round(completed / plan.items.length * 100) : 0
  const visibleItems = plan?.items.filter(item => dayFilter === 'all' || dayFilter === 'pending' && !item.completed || dayFilter === 'done' && item.completed) || []
  return <div className="course-panel-content plans-page">
    <div className="feature-heading"><div><h2>复习计划</h2><p>根据课程知识与学习记录，安排每日复习任务。</p></div><button className="button primary compact" onClick={() => { setError(''); setShowCreate(true) }}><Plus size={17}/>生成复习计划</button></div>
    {error && !showCreate && <ErrorNotice error={error}/>}
    {plans.loading && !plans.data ? <Spinner label="正在打开复习计划"/> : plans.error ? <ErrorNotice error={plans.error} retry={plans.reload}/> : !plan ? <div className="plan-empty panel">
      <Empty title="还没有复习计划" action={<button className="button primary" onClick={() => setShowCreate(true)}><Plus size={16}/>制定复习计划</button>}>选择计划周期与每日可投入的时间，把复习落实到具体任务。</Empty>
    </div> : <>
      <div className="plan-selector"><CalendarCheck2 size={17}/><select aria-label="查看历史复习计划" value={plan.id} onChange={event => { setSelected(event.target.value); setDayFilter('all') }}>{plans.data?.map(item => <option key={item.id} value={item.id}>{date(item.created_at)} · {item.days} 天计划 · {item.goal || '课程复习'}</option>)}</select><ChevronDown size={14}/></div>
      <section className="plan-summary">
        <div><h3>{plan.goal || '课程复习计划'}</h3><div className="plan-meta"><span><CalendarCheck2 size={14}/>{plan.days} 天复习</span><span><Clock3 size={14}/>每天 {plan.daily_minutes} 分钟</span><span>创建于 {date(plan.created_at)}</span></div><p>{plan.basis}</p></div>
        <div className="plan-progress"><span>已完成 {completed} / {plan.items.length} 项 · {progress}%</span><progress aria-label="复习计划完成进度" value={completed} max={plan.items.length || 1}/></div>
      </section>
      <div className="plan-list-heading"><h3>复习任务</h3><div className="filter-tabs" role="group" aria-label="计划任务筛选">{[['all', '全部任务'], ['pending', '待完成'], ['done', '已完成']].map(([key, label]) => <button key={key} aria-pressed={dayFilter === key} className={cn(dayFilter === key && 'active')} onClick={() => setDayFilter(key)}>{label}</button>)}</div></div>
      <div className="plan-items">{visibleItems.map(item => <article className={cn('plan-item', item.completed && 'completed')} key={item.id}>
        <button className="plan-check" role="checkbox" aria-checked={item.completed} aria-label={`${item.completed ? '取消完成' : '完成'}：第${item.day}天${item.title}`} disabled={!!updating} onClick={() => void toggle(item.id, !item.completed)}>{updating === item.id ? <LoaderCircle size={16} className="spin"/> : item.completed ? <Check size={16}/> : null}</button>
        <div className="plan-item-copy">
          <div className="plan-task-meta"><span>第 {item.day} 天</span><span><Clock3 size={14}/>{item.minutes} 分钟</span></div>
          <h4>{item.title}</h4>
          {item.description && <details className="plan-task-method"><summary>复习方法</summary><p>{item.description}</p></details>}
          <div className="concept-tags">{item.concepts.map(concept => <span key={concept}>{concept}</span>)}</div>
        </div>
      </article>)}</div>
      {!visibleItems.length && <Empty title={dayFilter === 'pending' ? '全部任务已完成' : '暂无已完成任务'}>可以切换筛选，查看这份计划的其他任务。</Empty>}
    </>}
    {showCreate && <Modal title="安排复习计划" onClose={() => !busy && setShowCreate(false)}><form className="modal-form" onSubmit={event => { event.preventDefault(); void create() }}>
      <p>计划将结合课程概念与已有练习记录。选择可投入的时间，再确定学习目标。</p>
      <div className="form-row"><label>计划周期<select value={days} onChange={event => setDays(Number(event.target.value))}><option value={7}>7 天 · 短期巩固</option><option value={14}>14 天 · 系统复习</option><option value={30}>30 天 · 深入掌握</option></select></label><label>每天投入时间<input type="number" value={minutes} onChange={event => setMinutes(Number(event.target.value))} min={15} max={180} step={5} required/><span className="field-hint">15–180 分钟</span></label></div>
      <label>学习目标<textarea value={goal} onChange={event => setGoal(event.target.value)} placeholder="例如：理解核心概念，查漏补缺，为期末考试做好准备。" rows={3} maxLength={500}/></label>
      {error && <ErrorNotice error={error}/>}
      <div className="notice">完成任务后勾选进度，历史计划可以随时查看。</div>
      <div className="modal-actions"><button type="button" className="button secondary" disabled={busy} onClick={() => setShowCreate(false)}>取消</button><button className="button primary" disabled={busy || minutes < 15 || minutes > 180}>{busy ? <LoaderCircle size={17} className="spin"/> : <CalendarCheck2 size={17}/>} {busy ? '正在制定计划' : '生成我的计划'}</button></div>
    </form></Modal>}
  </div>
}
