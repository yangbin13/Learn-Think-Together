import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { api, exportUrl } from '../api'
import type { Course } from '../api'
import { date, Empty, ErrorNotice, percentage, SectionTitle, Spinner, useResource } from '../components/ui'
import { CourseIcon } from './Library'

export default function Dashboard({ courses, openCourse }: { courses: Course[]; openCourse: (id: string, topic?: string) => void }) {
  const { data, error, loading, reload } = useResource(api.dashboard)
  const [allActivity, setAllActivity] = useState(false)
  const practiceCourse = courses.find(course => !course.is_builtin) || courses[0]
  if (loading) return <Spinner label="正在汇总学习记录"/>
  return <div className="dashboard-page">
    <SectionTitle title="学习仪表板" description="查看学习记录，找到需要巩固的知识。" action={<a href={exportUrl()} download className="button secondary">导出学习档案<ArrowRight size={16}/></a>}/>
    {error && <ErrorNotice error={error} retry={reload}/>}
    {data && <>
      <div className="dashboard-stats">
        {[
          { value: data.study_days, unit: '天', label: '有记录的学习日' },
          { value: data.questions_answered, unit: '次', label: '助教对话' },
          { value: data.practice_count, unit: '次', label: '已完成练习' },
          { value: data.accuracy == null ? '未测' : percentage(data.accuracy), unit: data.accuracy == null ? '' : '%', label: '练习正确率' },
        ].map(item => <div className="stat-card" key={item.label}><span>{item.label}</span><strong>{item.value}<small>{item.unit}</small></strong></div>)}
      </div>
      <section className="panel dashboard-courses">
        <div className="panel-heading"><h2>进入课程练习</h2></div>
        <div className="dashboard-course-grid">{courses.map(course => <button key={course.id} className="dashboard-course" onClick={() => openCourse(course.id)}>
          <span style={{ color: course.color }}><CourseIcon course={course}/></span>
          <div><strong>{course.title}</strong><small>{course.concept_count} 个概念 · {course.document_count} 份教材</small></div><ArrowRight size={17}/>
        </button>)}</div>
      </section>
      <div className="dashboard-columns">
        <section className="panel">
          <div className="panel-heading"><h2>待巩固的知识</h2></div>
          {data.weak_concepts.length ? <div className="weak-list">{data.weak_concepts.map((item, index) => <div className="weak-row" key={`${item.concept}-${index}`}>
            <div><strong>{item.concept}</strong><span>{item.course_title || '练习识别的薄弱概念'}{item.attempts != null && ` · ${item.attempts} 次作答`}</span></div>
            <div className="weak-meter"><span>{item.mastery == null ? '未测' : `${percentage(item.mastery)}%`}</span><div><i style={{ width: `${percentage(item.mastery)}%` }}/></div></div>
            {item.course_id && <button className="icon-button" aria-label={`练习${item.concept}`} onClick={() => openCourse(item.course_id!, item.concept)}><ArrowRight size={18}/></button>}
          </div>)}</div> : <Empty title="还没有练习结果" action={practiceCourse && <button className="button secondary" onClick={() => openCourse(practiceCourse.id)}>开始练习<ArrowRight size={16}/></button>}>完成一组练习后，这里会显示需要巩固的概念。</Empty>}
        </section>
        <section className="panel activity-panel">
          <div className="panel-heading"><h2>最近学习记录</h2></div>
          {data.recent_activity.length ? <>
            <div className={`activity-list${allActivity ? '' : ' activity-preview'}`}>{data.recent_activity.slice(0, allActivity ? undefined : 3).map((item, index) => <div className="activity-item" key={`${item.created_at}-${index}`}>
              <div><strong>{item.label}</strong><span>{item.type === 'chat' ? '助教对话' : item.type === 'quiz' || item.type === 'practice' ? '章节练习' : item.type === 'plan' ? '复习计划' : item.type === 'review' ? '复习任务' : '学习记录'}</span></div><time>{date(item.created_at)}</time>
            </div>)}</div>
            {data.recent_activity.length > 3 && <button className="text-link activity-toggle" aria-expanded={allActivity} onClick={() => setAllActivity(value => !value)}>{allActivity ? '收起记录' : `展开最近全部记录（${data.recent_activity.length}）`}</button>}
          </> : <Empty title="暂无学习记录">打开一门课程，与助教对话或完成练习。</Empty>}
        </section>
      </div>
    </>}
  </div>
}
