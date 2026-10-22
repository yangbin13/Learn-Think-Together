import { BookOpen, CalendarCheck2, ChartNoAxesCombined, MessageCircle, Network, PencilLine } from 'lucide-react'
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Citation, Course, PublicConfig } from '../api'
import { cn, Modal, percentage, Spinner, useResource } from '../components/ui'
import { readQuestionDraft, writeQuestionDraft } from '../lib/question-draft'
import Chat from './Chat'
import Practice from './Practice'
import Plans from './Plans'
import Materials, { CitationModal } from './Materials'
const Graph = lazy(() => import('./Graph'))
const tabs = [{ id: 'chat', label: '助教对话', icon: MessageCircle }, { id: 'materials', label: '课程教材', icon: BookOpen }, { id: 'graph', label: '思维导图', icon: Network }, { id: 'practice', label: '练习中心', icon: PencilLine }, { id: 'plans', label: '复习计划', icon: CalendarCheck2 }]
export default function CourseSpace({ userId, course, tab, topic, setTab, refreshCourses, config }: { userId: string; course: Course; tab: string; topic?: string; setTab: (tab: string) => void; refreshCourses: () => Promise<void>; config: PublicConfig | null }) {
  const overview = useResource(() => api.overview(course.id), [course.id]); const [citation, setCitation] = useState<Citation | null>(null); const [revision, setRevision] = useState(0)
  const changed = () => { overview.reload(); setRevision(value => value + 1) }; const materialsChanged = () => { changed(); void refreshCourses() }
  const [pendingQuestion, setPendingQuestion] = useState('')
  const acceptQuestion = (question: string) => { writeQuestionDraft(sessionStorage, userId, course.id, question); setPendingQuestion(''); setTab('chat') }
  const ask = (question: string) => {
    const existing = readQuestionDraft(sessionStorage, userId, course.id)
    if (existing.trim() && existing !== question) setPendingQuestion(question)
    else acceptQuestion(question)
  }
  const tabList = useRef<HTMLDivElement>(null)
  useEffect(() => { tabList.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }) }, [tab])
  return <div className="course-space"><div className="course-space-header"><div><h1>{course.title}</h1><p>{course.description}</p></div><div className="course-overview-mini"><span><ChartNoAxesCombined size={15}/>{overview.data?.mastery != null ? `${percentage(overview.data.mastery)}%` : '未测'}<small>概念掌握</small></span><span>{overview.data?.questions_answered ?? '—'}<small>次提问</small></span></div></div><div ref={tabList} className="course-tabs" role="tablist" aria-label="课程功能">{tabs.map(item => <button role="tab" aria-selected={tab === item.id} className={cn(tab === item.id && 'active')} key={item.id} onClick={() => setTab(item.id)}><item.icon size={17}/>{item.label}</button>)}</div><div className="course-tab-content">{tab === 'chat' && <div className="chat-layout"><Chat userId={userId} course={course} config={config} onComplete={changed} onCitation={setCitation}/><aside className="chat-materials"><Materials config={config} courseId={course.id} compact onChange={materialsChanged}/></aside></div>}{tab === 'materials' && <div className="course-panel-content"><Materials config={config} courseId={course.id} onChange={materialsChanged}/></div>}{tab === 'graph' && <Suspense fallback={<Spinner label="正在打开思维导图"/>}><Graph courseId={course.id} courseTitle={course.title} revision={revision} ask={ask}/></Suspense>} {tab === 'practice' && <Practice courseId={course.id} initialTopic={topic} onComplete={changed} onCitation={setCitation}/>} {tab === 'plans' && <Plans courseId={course.id} onChange={changed}/>} {!tabs.some(item => item.id === tab) && <div className="empty-state"><p>这个学习页面还不存在。</p><button className="button primary" onClick={() => setTab('chat')}>回到助教对话</button></div>}</div>{pendingQuestion && <Modal title="替换未发送的问题？" onClose={() => setPendingQuestion('')}><div className="modal-form"><p>你已有一份未发送的草稿。使用节点问题会替换它，请确认要继续的内容。</p><strong>当前草稿</strong><div className="source-excerpt">{readQuestionDraft(sessionStorage, userId, course.id)}</div><strong>节点问题</strong><div className="source-excerpt">{pendingQuestion}</div><div className="modal-actions"><button className="button secondary" onClick={() => setPendingQuestion('')}>保留原草稿</button><button className="button primary" onClick={() => acceptQuestion(pendingQuestion)}>使用节点问题</button></div></div></Modal>}{citation && <CitationModal citation={citation} close={() => setCitation(null)}/>}</div>
}
