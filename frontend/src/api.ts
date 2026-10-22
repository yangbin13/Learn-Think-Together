export type User = { id: string; username: string; display_name: string }
export type Course = { id: string; title: string; description: string; code: string; color: string; icon: 'network' | 'cpu' | 'layers' | 'globe'; assistant_name: string; is_builtin: boolean; document_count: number; chunk_count: number; concept_count: number }
export type IngestionProgress = { stage?: 'queued' | 'extracting' | 'chunking' | 'embedding' | 'persisting' | 'ready' | 'failed'; current_page?: number; total_pages?: number; chunk_count?: number; embedding_percent?: number; embedding_completed?: number; updated_at?: string }
export type MaterialQuality = { total_pages?: number; text_pages?: number; blank_pages?: number; short_pages?: number; character_count?: number; normalized_characters?: number; chapter_source?: string; outline_entries?: number; warnings?: string[]; page_kind?: 'physical' | 'logical'; semantic_status?: string; semantic_error?: string; semantic_note?: string; embedded_chunks?: number; normalization?: string }
export type Material = { id: string; course_id: string; title: string; filename: string; status: 'queued' | 'processing' | 'ready' | 'failed'; page_count: number; chunk_count: number; error: string | null; source_type: string; license: string; source_url: string | null; created_at: string; embedding_status?: 'lexical' | 'hybrid' | 'lexical_fallback'; progress?: IngestionProgress; quality?: MaterialQuality }
export type Chunk = { id: string; page: number | null; section: string; text: string }
export type Citation = { id: number | string; document_id: string; title: string; page: number | null; section: string; excerpt: string; source_type: string; chunk_id?: string; evidence_kind?: 'text' | 'image' | 'calculation'; vision_model?: string; page_kind?: 'physical' | 'logical' | 'calculation'; location_label?: string }
export type Trace = { node: string; status: string; label: string; detail: string; duration_ms?: number }
export type Message = { id: string; role: 'user' | 'assistant'; content: string; citations: Citation[]; trace: Trace[]; created_at: string; model?: string; grounded?: boolean | null }
export type Session = { id: string; title: string; created_at: string; updated_at: string }
export type GraphNode = { id: string; label: string; chapter: string; mastery: number | null; source: string; kind?: 'heading' | 'concept'; parent_id?: string | null; document_id?: string | null; page_start?: number | null; page_end?: number | null; page_kind?: 'physical' | 'logical' | null }
export type StructureNode = { id: string; parent_id: string | null; children: string[]; kind: 'document' | 'chapter' | 'section' | 'concept' | 'figure'; label: string; heading: string; depth: number; order: number; document_id: string | null; page_start: number | null; page_end: number | null; page_kind: 'physical' | 'logical' | null; source: string; concept_id: string | null; verified: boolean; mastery?: number | null; location_confidence?: string; display_label?: string; study_note?: string; annotation_updated_at?: string | null; figure_id?: string; figure_number?: string; caption?: string; origin?: 'auto' | 'manual' }
export type GraphEdge = { source: string; target: string; relation: string; kind?: 'structural' | 'sequence' | 'association'; source_label?: string; evidence?: string }
export type TextbookFigure = { figure_id: string; document_id: string; figure_number: string; raw_figure_number?: string; caption: string; caption_text?: string; caption_page: number; page_start: number; page_end: number; page_image_url: string; origin: 'auto' | 'manual'; verified: boolean }
export type FigureIndex = { document_id: string; status: 'not_indexed' | 'queued' | 'processing' | 'ready' | 'failed'; figure_count?: number; current_page?: number; total_pages?: number; error?: string | null; notice?: string }
export type GraphData = { nodes: GraphNode[]; edges: GraphEdge[]; tree: { root_ids: string[]; nodes: StructureNode[]; total_headings: number; total_figures?: number }; metadata?: { mode: string; total_headings: number; displayed_headings: number; graph_limit: number; sampled: boolean; total_figures?: number; figure_index?: FigureIndex[]; figure_notice?: string } }
export type MindMapEvidence = { node_id: string; heading: string; evidence: { document_id: string; title: string; page: number; section: string; excerpt: string; chunk_id: string; page_kind: 'physical' | 'logical' }[]; notice: string; figure?: TextbookFigure }
export type MindMapAnnotation = { node_id: string; display_label: string; study_note: string; updated_at: string | null }
export type WeakConcept = { concept: string; mastery: number | null; attempts?: number; course_id?: string; course_title?: string }
export type Activity = { type: string; label: string; created_at: string }
export type Overview = { mastery: number | null; questions_answered: number; practice_count: number; accuracy: number | null; weak_concepts: WeakConcept[]; recent_activity: Activity[]; documents: number; concepts: number }
export type DashboardData = { courses: number; questions_answered: number; practice_count: number; accuracy: number | null; study_days: number; recent_activity: Activity[]; weak_concepts: WeakConcept[] }
export type Question = { id: string; type: 'single_choice'; stem: string; options: { key: string; text: string }[]; concept: string; difficulty: string; concept_measured?: boolean; concept_notice?: string }
export type Quiz = { id: string; title: string; questions: Question[]; created_at: string; source: string; source_kind?: 'curated_bank' | 'textbook_generated'; draft_answers?: Record<string, string>; draft_version?: number }
export type QuizResult = { score: number; total: number; correct: number; results: { question_id: string; correct: boolean; user_answer: string; correct_answer: string; explanation: string; concept: string; citations?: Citation[]; concept_measured?: boolean; concept_notice?: string }[]; weak_concepts: string[]; next_action: string; unmapped_question_count?: number; measurement_notice?: string }
export type Mistake = { id: string; concept: string; stem: string; user_answer: string; correct_answer: string; explanation: string; created_at: string; resolved: boolean }
export type PracticeData = { quizzes: { id: string; title: string; score: number | null; total: number; created_at: string; submitted: boolean }[]; mistakes: Mistake[] }
export type PlanItem = { id: string; day: number; title: string; description: string; concepts: string[]; minutes: number; completed: boolean }
export type Plan = { id: string; goal: string; days: number; daily_minutes: number; items: PlanItem[]; created_at: string; basis: string }
export type PublicConfig = { provider: string; model: string; configured: boolean; embedding_model: string; agent_framework: string; max_upload_bytes?: number; vision_enabled?: boolean }
export class ApiError extends Error { status: number; constructor(message: string, status: number) { super(message); this.status = status } }
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { credentials: 'include', ...init, headers: { ...(init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...init?.headers } })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const message = typeof payload?.detail === 'string' ? payload.detail : typeof payload?.detail?.[0]?.msg === 'string' ? payload.detail[0].msg : `请求失败（${response.status}），请稍后重试。`
    if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('session-expired'))
    throw new ApiError(message, response.status)
  }
  return response.json() as Promise<T>
}
const post = <T>(path: string, body: unknown = {}) => request<T>(path, { method: 'POST', body: JSON.stringify(body) })
export const api = {
  me: () => request<User>('/auth/me'), login: (username: string, password: string) => post<User>('/auth/login', { username, password }), register: (username: string, password: string, display_name: string) => post<User>('/auth/register', { username, password, display_name }), demo: () => post<User>('/auth/demo'), logout: () => post<{ ok: boolean }>('/auth/logout'), config: () => request<PublicConfig>('/config/public'),
  courses: () => request<Course[]>('/courses'), course: (id: string) => request<Course>(`/courses/${encodeURIComponent(id)}`), createCourse: (title: string, description: string) => post<Course>('/courses', { title, description }), deleteCourse: (id: string) => request(`/courses/${id}`, { method: 'DELETE' }),
  dashboard: () => request<DashboardData>('/dashboard'), overview: (id: string) => request<Overview>(`/courses/${id}/overview`),
  documents: (id: string) => request<Material[]>(`/courses/${id}/documents`), chunks: (id: string, page = 1) => request<{ items: Chunk[]; total: number }>(`/documents/${id}/chunks?page=${page}`), deleteDocument: (id: string) => request(`/documents/${id}`, { method: 'DELETE' }),
  retryDocument: (id: string) => post<Material>(`/documents/${encodeURIComponent(id)}/retry`),
  addFigure: (id: string, physicalPage: number, figureNumber: string, caption: string) => post<TextbookFigure>(`/documents/${encodeURIComponent(id)}/figures`, { physical_page: physicalPage, figure_number: figureNumber, caption }),
  deleteFigure: (id: string, figureId: string) => request(`/documents/${encodeURIComponent(id)}/figures/${encodeURIComponent(figureId)}`, { method: 'DELETE' }),
  retryFigures: (id: string) => post(`/documents/${encodeURIComponent(id)}/figures/reindex`),
  saveMindmapAnnotation: (id: string, nodeId: string, displayLabel: string, studyNote: string) => request<MindMapAnnotation>(`/courses/${encodeURIComponent(id)}/mindmap/nodes/${encodeURIComponent(nodeId)}/annotation`, { method: 'PUT', body: JSON.stringify({ display_label: displayLabel, study_note: studyNote }) }),
  upload: (id: string, file: File) => { const form = new FormData(); form.append('file', file); form.append('rights_confirmed', 'true'); return request<Material>(`/courses/${id}/documents`, { method: 'POST', body: form }) },
  sessions: (id: string) => request<Session[]>(`/courses/${id}/sessions`), createSession: (id: string) => post<Session>(`/courses/${id}/sessions`), messages: (id: string) => request<Message[]>(`/sessions/${id}/messages`), deleteSession: (id: string) => request(`/sessions/${id}`, { method: 'DELETE' }),
  graph: (id: string) => request<GraphData>(`/courses/${id}/graph`), mindmapEvidence: (id: string, nodeId: string) => request<MindMapEvidence>(`/courses/${encodeURIComponent(id)}/mindmap/nodes/${encodeURIComponent(nodeId)}/evidence`), quiz: (id: string, topic: string, difficulty: string, count: number) => post<Quiz>(`/courses/${id}/quizzes`, { topic, difficulty, count }), submit: (id: string, answers: Record<string, string>) => post<QuizResult>(`/quizzes/${id}/submit`, { answers }), practice: (id: string) => request<PracticeData>(`/courses/${id}/practice`), resolve: (id: string) => post(`/mistakes/${id}/resolve`),
  resumeQuiz: (id: string) => request<Quiz & { submitted: boolean; result?: QuizResult }>(`/quizzes/${encodeURIComponent(id)}`),
  saveQuizDraft: (id: string, answers: Record<string, string>, expectedVersion: number) => request<{ draft_answers: Record<string, string>; draft_version: number }>(`/quizzes/${encodeURIComponent(id)}/draft`, { method: 'PUT', keepalive: true, body: JSON.stringify({ answers, expected_version: expectedVersion }) }),
  plans: (id: string) => request<Plan[]>(`/courses/${id}/plans`), createPlan: (id: string, days: number, daily_minutes: number, goal: string) => post<Plan>(`/courses/${id}/plans`, { days, daily_minutes, goal }), togglePlan: (id: string, item: string, completed: boolean) => request<Plan>(`/plans/${id}/items/${item}`, { method: 'PATCH', body: JSON.stringify({ completed }) }),
}
export type ChatMode = 'explain' | 'socratic' | 'solve'
export type ChatEvent = { type: string; data: Record<string, unknown> }
export async function streamChat(payload: { course_id: string; session_id?: string; message: string; mode: ChatMode }, onEvent: (event: ChatEvent) => void, signal: AbortSignal) {
  const response = await fetch('/api/chat', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal })
  if (!response.ok) { const data = await response.json().catch(() => ({})); if (response.status === 401) window.dispatchEvent(new Event('session-expired')); throw new ApiError(typeof data.detail === 'string' ? data.detail : `对话请求失败（${response.status}）`, response.status) }
  if (!response.body) throw new Error('浏览器无法读取实时回复，请刷新后重试。')
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let completed = false
  const parse = (block: string) => {
    let type = 'message'; const lines: string[] = []
    for (const line of block.split('\n')) { if (line.startsWith('event:')) type = line.slice(6).trim(); else if (line.startsWith('data:')) lines.push(line.slice(5).trimStart()) }
    if (!lines.length) return
    const data = JSON.parse(lines.join('\n')) as Record<string, unknown>
    onEvent({ type, data }); if (type === 'done') completed = true; if (type === 'error') throw new Error(String(data.message || '助教暂时无法回复，请重试。'))
  }
  try { while (true) { const { done, value } = await reader.read(); buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n'); let index: number; while ((index = buffer.indexOf('\n\n')) >= 0) { parse(buffer.slice(0, index)); buffer = buffer.slice(index + 2) } if (done) { if (buffer.trim()) parse(buffer); break } } if (!completed) throw new Error('连接提前结束，回复可能不完整。请重新发送问题。') } finally { reader.releaseLock() }
}
export const downloadUrl = (id: string) => `/api/documents/${encodeURIComponent(id)}/download`
export const previewUrl = (id: string, page?: number | null) => `/api/documents/${encodeURIComponent(id)}/preview${page && page > 0 ? `#page=${Math.floor(page)}` : ''}`
export const pageImageUrl = (id: string, page: number) => `/api/documents/${encodeURIComponent(id)}/pages/${Math.floor(page)}/image`
export const exportUrl = (id?: string) => `/api/export${id ? `?course_id=${encodeURIComponent(id)}` : ''}`
export const sourceLocation = (sourceType: string, page: number | null, section?: string) => { const physical = sourceType.endsWith('_pdf') || sourceType === 'open_textbook'; return [page ? physical ? `第 ${page} 物理页` : `第 ${page} 逻辑段` : '章节文本', section].filter(Boolean).join(' · ') }
