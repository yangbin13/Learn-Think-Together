import { useEffect, useRef, useState } from 'react'
import { ArrowRight, BookOpen, ChartNoAxesCombined, ChevronDown, CircleHelp, Download, LogOut, Menu, X } from 'lucide-react'
import { api, exportUrl } from './api'
import type { Course, PublicConfig, User } from './api'
import Auth, { Brand } from './views/Auth'
import Library from './views/Library'
import Dashboard from './views/Dashboard'
import CourseSpace from './views/CourseSpace'
import { cn, ErrorNotice, Modal, Spinner } from './components/ui'
type Route = { page: 'library' | 'dashboard' | 'course'; id?: string; tab?: string; topic?: string }
const readRoute = (): Route => {
  const [path, query = ''] = window.location.hash.replace(/^#\/?/, '').split('?')
  const parts = path.split('/')
  if (parts[0] !== 'course' || !parts[1]) return { page: parts[0] === 'dashboard' ? 'dashboard' : 'library' }
  let id = parts[1]
  // A pasted malformed URL must remain a recoverable missing-course screen.
  try { id = decodeURIComponent(id) } catch { /* Keep the unmatched raw ID. */ }
  return { page: 'course', id, tab: parts[2] || 'chat', topic: new URLSearchParams(query).get('topic') || undefined }
}
export default function App() {
  const [user, setUser] = useState<User | null>(null); const [booting, setBooting] = useState(true); const [bootError, setBootError] = useState(''); const [route, setRoute] = useState<Route>(readRoute); const [courses, setCourses] = useState<Course[]>([]); const [courseError, setCourseError] = useState(''); const [courseLoading, setCourseLoading] = useState(false); const [config, setConfig] = useState<PublicConfig | null>(null); const [help, setHelp] = useState(false); const [mobileNav, setMobileNav] = useState(false); const [loggingOut, setLoggingOut] = useState(false); const [logoutError, setLogoutError] = useState('')
  const sidebarRef = useRef<HTMLElement>(null)
  const workspaceRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!mobileNav || !user) return
    const query = window.matchMedia('(max-width: 700px)')
    if (!query.matches) { setMobileNav(false); return }
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    const workspace = workspaceRef.current
    document.body.style.overflow = 'hidden'
    if (workspace) workspace.inert = true
    sidebarRef.current?.querySelector<HTMLElement>('.mobile-only')?.focus()
    const trap = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setMobileNav(false); return }
      if (event.key !== 'Tab') return
      const items = [...(sidebarRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]') ?? [])].filter(item => item.getClientRects().length)
      const first = items[0]; const last = items[items.length - 1]
      if (!first) return
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    const resize = () => { if (!query.matches) setMobileNav(false) }
    window.addEventListener('keydown', trap, true); query.addEventListener('change', resize)
    return () => {
      document.body.style.overflow = overflow
      if (workspace) workspace.inert = false
      window.removeEventListener('keydown', trap, true); query.removeEventListener('change', resize)
      if (previous?.isConnected) previous.focus({ preventScroll: true })
    }
  }, [mobileNav, user])
  const boot = () => { setBooting(true); setBootError(''); api.me().then(setUser).catch(reason => { if (reason.status !== 401) setBootError(reason.message) }).finally(() => setBooting(false)) }
  useEffect(boot, [])
  useEffect(() => { const listener = () => setRoute(readRoute()); const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMobileNav(false) }; const expired = () => { setUser(null); setCourses([]); setLogoutError('') }; window.addEventListener('hashchange', listener); window.addEventListener('keydown', escape); window.addEventListener('session-expired', expired); return () => { window.removeEventListener('hashchange', listener); window.removeEventListener('keydown', escape); window.removeEventListener('session-expired', expired) } }, [])
  const loadCourses = async () => { setCourseLoading(true); setCourseError(''); try { setCourses(await api.courses()) } catch (reason) { setCourseError(reason instanceof Error ? reason.message : '课程加载失败') } finally { setCourseLoading(false) } }
  useEffect(() => { if (user) { void loadCourses(); api.config().then(setConfig).catch(() => {}) } }, [user])
  const navigate = (page: string, id?: string, tab = 'chat', topic?: string) => { window.location.hash = page === 'course' ? `course/${encodeURIComponent(id || '')}/${tab}${topic ? `?topic=${encodeURIComponent(topic)}` : ''}` : page; setMobileNav(false) }
  const logout = async () => { setLoggingOut(true); setLogoutError(''); try { await api.logout(); setUser(null); setCourses([]); window.location.hash = 'library' } catch (reason) { setLogoutError(reason instanceof Error ? reason.message : '退出失败') } finally { setLoggingOut(false) } }
  if (booting) return <div className="boot-screen"><Brand/><Spinner label="正在打开你的学习空间"/></div>
  if (!user) return <>{bootError && <div className="boot-error"><ErrorNotice error={bootError} retry={boot}/></div>}<Auth onAuth={setUser}/></>
  const currentCourse = courses.find(course => course.id === route.id)
  return <div className="app-shell"><button className="mobile-menu icon-button" aria-label="打开导航" aria-expanded={mobileNav} aria-controls="main-sidebar" onClick={() => setMobileNav(true)}><Menu size={23}/></button>{mobileNav && <div className="sidebar-overlay" onClick={() => setMobileNav(false)}/>}<aside ref={sidebarRef} role={mobileNav ? 'dialog' : undefined} aria-modal={mobileNav || undefined} aria-label="学习导航" id="main-sidebar" className={cn('sidebar', mobileNav && 'sidebar-open')}><div className="sidebar-brand"><Brand/><button aria-label="关闭导航" className="icon-button mobile-only" onClick={() => setMobileNav(false)}><X size={20}/></button></div><div className="sidebar-slogan">专业课程学习工作台</div><div className="nav-label">学习空间</div><nav aria-label="主导航"><button className={cn('nav-item', route.page === 'library' && 'active')} onClick={() => navigate('library')}><BookOpen size={20}/>我的课程<span className="nav-count">{courses.length}</span></button><button className={cn('nav-item', route.page === 'dashboard' && 'active')} onClick={() => navigate('dashboard')}><ChartNoAxesCombined size={20}/>学习仪表板</button></nav><div className="nav-label course-nav-label">我的助教 <ChevronDown size={12}/></div><nav aria-label="课程助教" className="course-nav">{courses.map(course => <button key={course.id} className={cn('course-nav-item', route.id === course.id && route.page === 'course' && 'active')} onClick={() => navigate('course', course.id)}><span className="course-dot" style={{ background: course.color }}/><span>{course.title}</span><ArrowRight size={13}/></button>)}</nav><div className="sidebar-bottom"><button className="nav-item" onClick={() => { setMobileNav(false); setHelp(true) }}><CircleHelp size={18}/>使用指南</button><div className="user-row"><div className="avatar">{user.display_name.slice(0, 1)}</div><div><strong>{user.display_name}</strong><span>@{user.username}</span></div><button className="icon-button" title="退出登录" aria-label="退出登录" disabled={loggingOut} onClick={() => void logout()}><LogOut size={17}/></button></div></div></aside><div ref={workspaceRef} className="workspace"><header className="workspace-header"><div className="breadcrumbs"><span>学习空间</span><span>/</span><strong>{route.page === 'course' ? currentCourse?.title || '课程助教' : route.page === 'dashboard' ? '学习仪表板' : '我的课程'}</strong></div><div className="header-tools"><span className={cn('provider-state', config && !config.configured && 'warning')}><span className="small-dot"/>{config?.configured ? '助教已就绪' : config ? '模型待配置' : '学习工作台'}</span><a className="export-button" href={exportUrl(route.page === 'course' ? route.id : undefined)} download><Download size={15}/><span>导出学习记录</span></a></div></header>{logoutError && <ErrorNotice error={logoutError}/>}<main className={cn('main-content', route.page === 'course' && 'course-main')}>{courseError && <ErrorNotice error={courseError} retry={() => void loadCourses()}/>} {route.page === 'library' && <Library user={user} courses={courses} loading={courseLoading} refresh={loadCourses} openCourse={id => navigate('course', id)}/>} {route.page === 'dashboard' && <Dashboard courses={courses} openCourse={(id, topic) => navigate('course', id, 'practice', topic)}/>} {route.page === 'course' && (courseLoading && !currentCourse ? <Spinner/> : currentCourse ? <CourseSpace key={currentCourse.id} userId={user.id} course={currentCourse} tab={route.tab || 'chat'} topic={route.topic} setTab={tab => navigate('course', currentCourse.id, tab)} refreshCourses={loadCourses} config={config}/> : <div className="not-found"><h2>未找到这门课程</h2><button className="button primary" onClick={() => navigate('library')}>返回我的课程</button></div>)}</main><footer className="workspace-footer"><span>THINKMATE <span className="footer-dot">·</span> 学思同行</span><span>AI 的回答可能有误，请结合教材核对。</span></footer></div>{help && <Modal title="使用指南" onClose={() => setHelp(false)}><div className="help-content"><p>四门 408 课程提供开放授权的全文电子教材、原创导学手册与知识框架。也可以创建自己的专业课，上传有权使用的课本，让助教围绕你的教材展开。</p><ol><li><strong>准备教材</strong><p>在「课程教材」上传 PDF、Word、TXT 或 Markdown，等待解析完成。扫描 PDF 可能无法提取文字，可改用文本版。</p></li><li><strong>和助教对话</strong><p>选择讲解、启发或解题模式。展开回答的「检索与工具记录」可查看检索流程，点击引用查看证据原文。</p></li><li><strong>以练促学</strong><p>用章节练习检验理解，做错的题目进入错题记录。概念掌握度随你的练习更新。</p></li><li><strong>安排复习</strong><p>按可投入的时间生成复习计划，完成后勾选任务。导出按钮可保存 Markdown 学习记录。</p></li></ol><div className="notice">上传仅限你拥有使用权的学习资料。内置资料的授权与来源可在教材详情查看。</div></div></Modal>}</div>
}
