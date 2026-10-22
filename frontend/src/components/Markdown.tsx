import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
export function normalizeMath(content: string): string {
  // Preserve fenced code and inline code byte-for-byte; normalize only prose.
  const code = /(`{3,}[^\n]*\n[\s\S]*?^`{3,}\s*$|~{3,}[^\n]*\n[\s\S]*?^~{3,}\s*$|(`+)[\s\S]*?\2)/gm
  const parts: string[] = []; let start = 0; let match: RegExpExecArray | null
  const prose = (value: string) => value.replace(/\\\[([\s\S]*?)\\\]/g, (_, expression: string) => `\n$$\n${expression.trim()}\n$$\n`).replace(/\\\(([\s\S]*?)\\\)/g, (_, expression: string) => `$${expression.trim()}$`)
  while ((match = code.exec(content))) { parts.push(prose(content.slice(start, match.index)), match[0]); start = match.index + match[0].length }
  parts.push(prose(content.slice(start))); return parts.join('')
}
export default function Markdown({ content }: { content: string }) { return <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={{ a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> }}>{normalizeMath(content)}</ReactMarkdown></div> }
