export type TraceEventStatus = { node: string; status: string }
export type TraceDisplayState = 'running' | 'started' | 'completed' | 'failed' | 'degraded' | 'unknown'

export const traceStateLabels: Record<TraceDisplayState, string> = {
  running: '进行中',
  started: '开始记录',
  completed: '已完成',
  failed: '失败',
  degraded: '继续学习',
  unknown: '状态未确认',
}

/** Trace entries are events: an old start is neither a live task nor proof of success. */
export function traceDisplayStates(items: readonly TraceEventStatus[], pending: boolean): TraceDisplayState[] {
  const states = new Array<TraceDisplayState>(items.length)
  const laterNodes = new Set<string>()
  for (let index = items.length - 1; index >= 0; index--) {
    const item = items[index]
    if (['running', 'started', 'pending'].includes(item.status)) {
      states[index] = pending && !laterNodes.has(item.node) ? 'running' : 'started'
    } else if (item.status === 'completed') {
      states[index] = 'completed'
    } else if (item.status === 'failed') {
      states[index] = 'failed'
    } else if (item.status === 'degraded') {
      states[index] = 'degraded'
    } else {
      states[index] = 'unknown'
    }
    laterNodes.add(item.node)
  }
  return states
}
