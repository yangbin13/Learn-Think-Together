import { useState } from 'react'
import { ArrowRight, BookOpen, Layers, LoaderCircle } from 'lucide-react'
import { api } from '../api'
import type { User } from '../api'
import { cn, ErrorNotice } from '../components/ui'

export function Brand({ light = false }: { light?: boolean }) {
  return <div className={cn('brand', light && 'brand-light')}>
    <span className="brand-mark"><BookOpen size={22}/></span>
    <div><strong>学思同行</strong><small>ThinkMate</small></div>
  </div>
}

export default function Auth({ onAuth }: { onAuth: (user: User) => void }) {
  const [register, setRegister] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const authenticate = async (demo = false) => {
    setBusy(demo ? 'demo' : 'form'); setError('')
    try {
      onAuth(demo ? await api.demo() : register ? await api.register(username, password, name) : await api.login(username, password))
    } catch (reason) { setError(reason instanceof Error ? reason.message : '暂时无法登录') }
    finally { setBusy('') }
  }
  return <div className="auth-page">
    <aside className="auth-story">
      <Brand light/>
      <div className="story-content">
        <h1>围绕教材，<br/>学懂专业课。</h1>
        <p>选择一门课程，带着问题开始学习。<br/>408 四科与自己的专业课程，都在这里。</p>
        <ol className="auth-workflow">
          <li><strong>准备教材</strong><span>使用内置开放教材，或上传自己的课本。</span></li>
          <li><strong>提问与核对</strong><span>与课程助教讨论，打开引用查看原文。</span></li>
          <li><strong>练习与复习</strong><span>记录错题与掌握度，安排下一次复习。</span></li>
        </ol>
      </div>
      <p className="story-foot">数据按账户与课程保存。</p>
    </aside>
    <main className="auth-form-side">
      <div className="auth-card">
        <h2>{register ? '创建学习账户' : '登录学习空间'}</h2>
        <p>{register ? '保存你的教材、对话和学习记录。' : '继续上次的课程与对话。'}</p>
        <div className="auth-tabs" role="group" aria-label="账户操作">
          <button className={cn(!register && 'active')} aria-pressed={!register} onClick={() => { setRegister(false); setError('') }}>登录</button>
          <button className={cn(register && 'active')} aria-pressed={register} onClick={() => { setRegister(true); setError('') }}>注册</button>
        </div>
        <form onSubmit={event => { event.preventDefault(); void authenticate() }}>
          <label>用户名<input autoComplete="username" name="username" placeholder="输入用户名" value={username} onChange={event => setUsername(event.target.value)} required minLength={3} maxLength={40}/></label>
          {register && <label>怎么称呼你<input name="display_name" autoComplete="nickname" placeholder="例如：小林" value={name} onChange={event => setName(event.target.value)} required maxLength={40}/></label>}
          <label>密码<input type="password" autoComplete={register ? 'new-password' : 'current-password'} name="password" placeholder={register ? '至少 8 位字符' : '输入密码'} value={password} onChange={event => setPassword(event.target.value)} required minLength={register ? 8 : 1}/></label>
          {error && <ErrorNotice error={error}/>}
          <button className="button primary auth-submit" disabled={!!busy} type="submit">
            {busy === 'form' ? <><LoaderCircle size={17} className="spin"/>{register ? '正在创建' : '正在登录'}</> : <>{register ? '创建学习空间' : '进入学习空间'}<ArrowRight size={18}/></>}
          </button>
        </form>
        <div className="auth-divider"><span>首次使用</span></div>
        <button disabled={!!busy} className="button secondary demo-button" onClick={() => void authenticate(true)}>
          {busy === 'demo' ? <LoaderCircle size={17} className="spin"/> : <Layers size={17}/>}体验学习工作台
        </button>
        <p className="auth-hint">体验会创建独立游客账户。已有账户请登录，以便查看之前的教材与记录。</p>
      </div>
    </main>
  </div>
}
