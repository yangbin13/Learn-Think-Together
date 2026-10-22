# 内置开放电子教材：来源、许可与覆盖范围

核验日期：2026-10-02。四门 408 助教分别绑定一部完整英文开放教材与项目原创中文导学手册。开放教材是合法可再分发的学习参考，不是国内出版社指定 408 教材，也不声称逐条覆盖最新 408 考纲。用户可在自己的课程空间上传已获授权教材。

目录元数据在 `content/open-textbooks/manifest.json`，包含课程 ID、相对路径、标题、来源类型、许可、原下载地址、许可依据、页数、体积与 SHA-256。所有 PDF 保持原始字节，不改写、不去除版权页；许可及署名在 `content/open-textbooks/licenses/`。系统显示及下载时应保留作者、原始来源与许可链接。

## 逐本映射

| 课程 ID | 书名与作者 | 项目文件 | 页数 | 许可 |
| --- | --- | --- | ---: | --- |
| `data-structures` | Open Data Structures, C++ Edition — Pat Morin | `content/open-textbooks/open-data-structures.pdf` | 336 | CC BY 2.5 Canada |
| `computer-organization` | Computer Organization (2021) — Suk Jin Lee | `content/open-textbooks/computer-organization.pdf` | 171 | CC BY 4.0 International |
| `operating-systems` | xv6: a simple, Unix-like teaching operating system, RISC-V revision 5 — Russ Cox, Frans Kaashoek, Robert Morris | `content/open-textbooks/xv6-riscv-book.pdf` | 116 | MIT；作用于 book sources 与 associated documentation |
| `computer-networks` | Computer Networking: Principles, Protocols and Practice, Third Edition — Olivier Bonaventure 及署名协作者 | `content/open-textbooks/computer-networking-principles.pdf` | 413 | CC BY 3.0；2021 PDF 第 7 页版权声明 |

### 数据结构

- 出版/作者页：[Open Data Structures](https://www.opendatastructures.org/)。页面明确允许复制、分发、使用和改编，包括商业使用，并将许可链接指向 [CC BY 2.5 Canada](https://creativecommons.org/licenses/by/2.5/ca/)。
- 官方 PDF：[C++ Edition](https://www.opendatastructures.org/ods-cpp.pdf)。PDF 第 11 页“Why This Book?”也说明开放许可和署名要求；该页是文件物理页码，与书内罗马页码不同。
- 许可文本：`licenses/CC-BY-2.5-CA.html`；[官方 legal code](https://creativecommons.org/licenses/by/2.5/ca/legalcode.en)。再分发保留作者、标题、来源和许可；改编需标识，不暗示作者为本项目背书。
- 覆盖：列表、栈/队列、哈希、搜索树、堆、图及复杂度等；部分 408 专项知识及中文术语由原创导学手册补充，英文教材不冒充中文考试官方用书。

### 计算机组成原理

- 发布机构：[University System of Georgia / OpenALG](https://alg.manifoldapp.org/projects/computer-organization)。项目页列出作者 Suk Jin Lee，13 章完整目录，公开整本 PDF，Metadata rights 明确为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。
- PDF 是项目页“Download .pdf”指向的 [官方 CDN 文件](https://cdn.alg.manifoldapp.org/store/actioncallout/9/7/0/970f49e0-fd75-4e2f-8685-25ac473ad767/attachment/eef4257951b0e08432216ab0cfcd3fb0.pdf)。文件标题页为 2021 年 CPSC 2105，Columbus State University，171 页。
- 许可文本：`licenses/CC-BY-4.0.txt`。必须署名、保留许可和来源、标识修改；原 PDF 未修改。
- 覆盖：逻辑门、布尔表达式、数制、计算机运算、时序电路、CPU、指令体系、汇编、流水线、内存、虚拟内存。适合作为计组课程参考；不声明完全覆盖 408 所有考点。

### 操作系统

- MIT 课程发布页：[xv6 teaching operating system](https://pdos.csail.mit.edu/6.828/2025/xv6.html)，明确分别链接 OS 源码与书籍源码。
- 官方 PDF：[RISC-V book revision 5](https://pdos.csail.mit.edu/6.1810/2025/xv6/book-riscv-rev5.pdf)，116 页。
- 许可依据是 [mit-pdos/xv6-riscv-book/LICENSE](https://github.com/mit-pdos/xv6-riscv-book/blob/xv6-riscv/LICENSE)，而不是 xv6 OS code 的许可。原文允许 Book 及相关文档的使用、修改与分发，并要求保留版权及许可通知。完整原文保存为 `licenses/xv6-book-MIT.txt`，署名版权为 2006—2024 Russ Cox、Frans Kaashoek、Robert Morris，Massachusetts Institute of Technology。
- 覆盖：实际 OS 的进程、系统调用、页表、陷阱、锁、调度、文件系统等；是教学内核实现教材，不是面向 408 题型的全面考试教材。

### 计算机网络

- 作者官网：[Computer Networking: Principles, Protocols and Practice](https://www.computer-networking.info/)；官网直接列出第三版电子书 PDF 和 GitHub release。
- 官方 PDF 原链接：[CNP3-2021.pdf](https://www.computer-networking.info/_downloads/d2fa2bcdefb52011ae73bffe908ba250/CNP3-2021.pdf)；同页公开的 [GitHub release 镜像](https://github.com/cnp3/ebook/releases/download/draft-3rd/CNP3-2021.pdf) 可用于下载兜底。实际使用来源以 manifest 记录为准。
- 实际下载的 2021 PDF 第 7 页（书内页码 3）版权声明为 2011—2021 Olivier Bonaventure、Université catholique de Louvain (Belgium) 及该页列出的协作者，并明确指向 [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)。完整条款保存 `licenses/CC-BY-3.0.html`。原 PDF 未修改，必须保留署名、来源、许可与改编说明。
- 当前作者源码仓库 [cnp3/ebook README](https://github.com/cnp3/ebook) 为 CC BY-SA 3.0，和这部已发布 2021 PDF 的版权声明不同。本项目仅分发官方发布的原 PDF，按其自身许可记录；不复制最新版源仓库内容。备用源码许可文本保存在 `licenses/CC-BY-SA-3.0.html` 以保留核验差异，未来若使用最新版源码或其改编，需重新遵守其 ShareAlike 条件。教材作为独立文件与应用代码组成集合，不把教材许可写成应用代码许可。
- 覆盖：网络基础、可靠传输、路由、HTTP/DNS/TLS、TCP/UDP、IPv6、BGP/RIP/OSPF、以太网/Wi-Fi、练习。教材版本有其范围和发布时间，不代表所有最新协议或国内考试细则。

## 未采用的候选

- OSTEP：本次不下载、不随项目再分发，尊重作者的仅链接要求。
- Bob Plantz 的旧版 Introduction to Computer Organization：现官网说明旧版只能在线使用；本次未找到作者当前允许随项目完整 PDF 再分发的明确依据，未纳入。
- Dive Into Systems：官网 Book Version 页实际是 CC BY-NC-ND 4.0，限制商业及改编，未纳入默认可改编教学材料集合。
- David Tarnoff 旧版 Computer Organization and Design Fundamentals：虽然作者提供免费下载，旧版 PDF 版权页写 All rights reserved。ETSU 的 CC BY-NC-SA 4.0 页面明确指向后来的音视频 Series，不能外推到旧版 PDF，未纳入。

## 验证边界

下载校验检查 PDF 文件头、pypdf 可打开、物理页数与 SHA-256。上述是文件完整性和许可证据检查，不是逐页内容校对或 408 全覆盖评估。模型回答必须仍基于实际检索片段；不能因为资料许可允许分发就把模型结论视为教材确认。
