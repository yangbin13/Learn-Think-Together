import type { ChatMode } from '../api'

const subjectPrompts: Record<string, string[]> = {
  'data-structures': ['用一个生活例子解释栈和队列的区别', '二叉搜索树为什么可能退化？', '怎么比较不同排序算法的适用场景？'],
  'computer-organization': ['Cache 命中率如何影响访存时间？', '补码为什么能统一加法和减法？', '流水线中的数据冒险怎么解决？'],
  'operating-systems': ['进程和线程到底有什么区别？', '死锁的四个必要条件是什么？', '为什么需要虚拟内存？'],
  'computer-networks': ['TCP 三次握手分别在确认什么？', '子网掩码与 IP 地址如何配合？', '从输入网址到页面显示经历了什么？'],
}

export function starterPrompts(courseId: string, courseTitle: string, mode: ChatMode): string[] {
  if (mode === 'socratic') {
    return [
      '从教材选一个具体概念，只问我一个可回答的入门问题；暂时不要讲解结论',
      '用一个生活情境引出教材中的概念，再只问我一个问题；暂时不要解释答案',
      '根据教材内容，先只问我一个问题，帮我确定从哪个主题开始学',
    ]
  }
  return subjectPrompts[courseId] || [
    `请依据教材目录，概括${courseTitle}的三个主要学习模块，并标注对应章节`,
    '请从教材选择一个重点概念，说明出处，并用一个生活例子解释',
    '请根据教材目录，推荐前三个学习主题及顺序，并说明理由',
  ]
}
