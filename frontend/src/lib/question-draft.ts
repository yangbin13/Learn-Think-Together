type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
const key = (userId: string, courseId: string) => `thinkmate-question-v2:${encodeURIComponent(userId)}:${encodeURIComponent(courseId)}`

/** A question is private to one student, one course and this browser tab. */
export function readQuestionDraft(storage: DraftStorage, userId: string, courseId: string): string {
  try { return (storage.getItem(key(userId, courseId)) || '').slice(0, 8000) } catch { return '' }
}

export function writeQuestionDraft(storage: DraftStorage, userId: string, courseId: string, value: string): boolean {
  try {
    if (value.trim()) storage.setItem(key(userId, courseId), value.slice(0, 8000))
    else storage.removeItem(key(userId, courseId))
    return true
  } catch { return false }
}
