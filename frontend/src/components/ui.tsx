import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { AlertCircle, ArrowRight, BookOpen, Check, LoaderCircle, X } from 'lucide-react'
export const cn = (...values: (string | false | null | undefined)[]) => values.filter(Boolean).join(' ')
export const date = (value: string) => new Date(value).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
export const percentage = (value: number | null) => value == null ? 0 : Math.round(value <= 1 ? value * 100 : value)
export function Spinner({ label = '正在加载' }: { label?: string }) { return <div className="loading" role="status"><LoaderCircle size={20} className="spin"/><span>{label}…</span></div> }
export function ErrorNotice({ error, retry }: { error: string; retry?: () => void }) { return <div className="error-notice" role="alert"><AlertCircle size={18}/><span>{error}</span>{retry && <button onClick={retry}>重试</button>}</div> }
export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) { return <div className="empty-state"><div className="empty-icon"><BookOpen size={27}/></div><h3>{title}</h3>{children && <p>{children}</p>}{action}</div> }
export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose); closeRef.current = onClose
  useLayoutEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const initial = ref.current?.querySelector<HTMLElement>('[data-dialog-autofocus]') ?? ref.current
    initial?.focus()
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); closeRef.current() }
      if (event.key !== 'Tab') return
      const items = [...(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]') ?? [])].filter(item => item.getClientRects().length)
      if (!items.length) { event.preventDefault(); ref.current?.focus(); return }
      const first = items[0]; const last = items[items.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', listener)
    return () => {
      document.body.style.overflow = overflow
      document.removeEventListener('keydown', listener)
      if (previous?.isConnected) previous.focus({ preventScroll: true })
    }
  }, [])
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}><div ref={ref} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} className={cn('modal', wide && 'modal-wide')}><div className="modal-head"><h2>{title}</h2><button className="icon-button" aria-label="关闭弹窗" onClick={onClose}><X size={20}/></button></div>{children}</div></div>
}
export function useResource<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null); const [error, setError] = useState(''); const [loading, setLoading] = useState(true); const [revision, setRevision] = useState(0)
  useEffect(() => { let active = true; setLoading(true); setError(''); loader().then(value => { if (active) setData(value) }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : '加载失败，请重试') }).finally(() => { if (active) setLoading(false) }); return () => { active = false } }, [...deps, revision]) // Loader follows explicit dependencies.
  return { data, setData, error, loading, reload: () => setRevision(value => value + 1) }
}
export function SectionTitle({ title, description, action }: { title: string; description?: string; action?: ReactNode }) { return <div className="section-title"><div><h1>{title}</h1>{description && <p>{description}</p>}</div>{action}</div> }
export function Success({ children }: { children: ReactNode }) { return <div className="success-notice" role="status"><Check size={17}/>{children}</div> }
export function TextLink({ children, onClick }: { children: ReactNode; onClick: () => void }) { return <button className="text-link" onClick={onClick}>{children}<ArrowRight size={15}/></button> }
