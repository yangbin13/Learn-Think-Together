# 学思同行 ThinkMate

> 本文件是源码项目说明。GitHub 仓库采用五类材料目录，本项目位于 `02-源代码/学思同行/`；总入口见[仓库 README](../../README.md)。下文的“根目录”与相对路径均指本源码目录，所有启动、配置、测试命令从这里执行。

把学生正在使用的教材、提问和练习记录连接起来的专业课助教。参赛方向为中兴赛道命题二“专业课助教智能体”。

学生可使用内置数据结构、计算机组成原理、操作系统、计算机网络四科，也可创建课程并上传自己有权使用的资料。当前生成、审核、出题和页图均使用 mimo-v2.6-flash；可选 BGE-M3 嵌入服务独立配置。模型凭证只在后端读取。

## 主要功能

- **三种教学模式**：讲解直接说明概念；启发模式用可回答的一步问题引导，学生尝试后先判断再继续；解题按题给条件展示步骤和结果。当前明确请求优先于旧对话的临时要求。
- **实际教材依据**：课程范围检索、整书目录/前言概览、相邻页精读和原文引用。PDF 显示物理页，DOCX/TXT/MD 显示逻辑段；可展开引用并打开本人原文件。
- **受限工具**：已有教材正文足够的基础问题直接教学；数值和算法任务可实际执行安全算术、二分轨迹、分页地址、调度、银行家、页置换、子网、最短路径和 Cache 等工具。工具卡保存本轮实际输入和结果。
- **完整资料管理**：PDF/DOCX/TXT/MD，默认每份 200 MiB、PDF 最多 2,000 页；摄取进度、质量提示、可选向量、失败重试、缺失向量续建、下载和删除。
- **课程思维导图与图示**：章节树、分支展开、搜索、个人笔记、主动回忆、图题定位和本人补充图示，支持查看原页。结构表达包含关系，不冒称专家审核的先修图谱。
- **按需页图核对**：对已定位或学生明确指定的实际 PDF 页调用视觉模型，标注观察和未确认内容。单轮最多两页，可打开原页复核。
- **练习与复习闭环**：内置 408 自编题库；自建课程按真实教材出题，先检验来源，再盲解与解析审核。提交前隐藏答案；草稿、评分、错题、实测概念统计、阶段计划和导出持久保存。
- **诚实恢复**：内部检索、工具、格式或复核失败后，尝试一次通用模型回复，说明教材/工具尚未核实；上游仍不可用时提供学习续接提示。额度和限流明确提示，失败草稿和伪引用不沿用。
- **账号与课程隔离**：注册/登录、无需固定密码的演示入口；服务端检查私有资料、会话、练习、计划和原页所有者。

通用模型已经能回答许多专业课问题。本项目增加的价值是实际教材对齐、接着学生当前一步反馈、核对具体条件和保存连续学习记录。没有做豆包产品对照，不声称全面优于通用助手。

## 参赛材料与仓库

仓库：[yangbin13/Learn-Think-Together](https://github.com/yangbin13/Learn-Think-Together)。六项作品材料均可从仓库定位：

| 比赛要求 | 仓库入口 |
| --- | --- |
| 可运行作品及部署方式 | backend/、frontend/、scripts/start.ps1、scripts/start.sh、Dockerfile、compose.yaml；运行后点击“体验学习工作台” |
| 技术文档 | [Word](docs/学思同行-项目技术与验收报告-20261011.docx)、[PDF](docs/学思同行-项目技术与验收报告-20261011.pdf)、[可检索正文](docs/submission-report.md) |
| README | 本文件：配置、安装、启动、运行、测试与常见问题 |
| 至少10条正常、异常、边界用例 | [32条最终测试用例](docs/test-cases-final.md)：正常17、异常8、边界7；[逐条证据](docs/validation/test-case-coverage-20261011.json) |
| 3–5分钟演示视频 | [4分31秒演示MP4](docs/demo/学思同行-最终演示-20261011.mp4)、[字幕](docs/demo/学思同行-最终演示-20261011.srt)、[解说与复现说明](docs/demo/学思同行-演示解说与复现-20261011.md) |
| 合规说明 | [单一中文合规文档](docs/compliance.md)：数据来源、隐私保护、个人信息与脱敏情况 |

[三模式回答质量与实测](docs/starter-prompts-20261011.md) · [提交检查清单](docs/submission-checklist.md)。仓库通过依赖锁文件安装并构建前端；生成的 frontend/dist 保留为本机构建目录。

私人数据库、用户教材原文、Cookie、访问令牌和实际 API 凭证不分发。个人报名材料由参赛者单独提供给组委会，不进入源码与公开视频。最终视频使用原创内置 408 资料；此前私人完整教材验收只公开必要摘要。

## 快速运行 Windows PowerShell

需要 Python 3.12+、Node.js 22+；首次安装依赖和模型调用需要网络。先克隆并进入 `02-源代码/学思同行/` 源码项目目录，下面的配置与启动命令均在该源码目录执行：

~~~powershell
git clone https://github.com/yangbin13/Learn-Think-Together.git
cd Learn-Think-Together
cd "02-源代码/学思同行"
~~~

### 第一步：配置 `.env`

后端读取**源码项目根目录**的 `.env`。首次使用在该目录执行以下命令，已有配置会保留：

~~~powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
~~~

用文本编辑器打开 `.env`，先确认下列三个参数：

| 配置项 | 随包示例 | 使用者需要做什么 |
| --- | --- | --- |
| `LLM_API_KEY` | 留空 | **启用在线 AI 必填**，mimo模型控制台申请key：https://platform.xiaomimimo.com/console/api-keys |
| `LLM_API_BASE` | `https://api.xiaomimimo.com/v1` | 普通 MiMo 接口可保留示例；不同平台或 Token Plan 按套餐提供的接口基础地址修改。 |
| `LLM_MODEL` | `mimo-v2.6-flash` | 使用该模型可保留示例；更换模型时填平台实际支持的模型 ID。 |

**使用随包的普通 MiMo 地址和模型时，只需填写 `LLM_API_KEY`，其余保持默认即可。**

**注：也可以替换其它平台的多模态模型（要包含视觉能力，纯文本模型不符合要求）**

 教学、工具规划、教材出题和审核默认共用主模型；`LLM_QUIZ_MODEL`、`LLM_REVIEW_MODEL`、`VISION_MODEL` 可留空。

示例已将 `EMBEDDING_ENABLED=false`，教材检索使用本地中文 BM25，不要求另一份向量模型凭证。只有主动启用语义向量检索时，才需要配置独立的 `EMBEDDING_API_BASE` 和 `EMBEDDING_API_KEY`。高级选项见下方“配置”章节。

保存 `.env` 后启动服务；运行中的配置修改需要重启后端。未配置有效在线模型时，教材、原创题库、导图、计划和记录仍可体验，页面会如实区分学习续接提示与模型答复。

### 第二步：启动与进入工作台

1. 执行一键启动：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
~~~

2. 打开 [本机应用](http://127.0.0.1:8765)，点击“体验学习工作台”，或注册自己的账号。新演示入口创建独立账号，没有需要公开的固定密码。

已有依赖和生产构建时：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -SkipInstall -Port 8766
~~~

端口占用时选择空闲端口，脚本不会停止其他进程。Ctrl+C 停止本次服务。当前工作区验收使用 8766，源码默认 8765。

无有效模型凭证时，资料、原创内置练习和计划仍可体验；在线 AI 会说明不可用。学习续接提示不作为在线知识答案。

## 手动安装与开发

~~~powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.lock.txt
cd frontend
npm ci
npm run build
cd ..
.venv/Scripts/python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
~~~

FastAPI 同时托管生产前端，访问一个端口即可。开发界面时在 frontend 另开终端执行 npm run dev，访问 5173，/api 代理到 8765。Linux 提供 sh scripts/start.sh，原生环境兼容性以实际主机验证为准。

## Docker

~~~powershell
docker compose -p thinkmate up --build -d
docker compose -p thinkmate logs -f tutor
docker compose -p thinkmate down
~~~

`-p thinkmate` 固定 Compose 项目名，适用于当前纯中文源码目录；日志与停止命令使用相同参数。compose.yaml 默认映射 127.0.0.1:8765，数据保存在 tutor-data 命名卷。停止不加 -v；删除卷会删除学习数据。历史 Linux 容器与换容器持久化证据保留，最新代码复现范围见最终验收。公共部署需配置 HTTPS、备份、监控与容量方案。

Docker 使用相同的源码根目录 `.env`，先完成上面的最小配置，再执行 Compose 命令。仓库提供 `Dockerfile` 与 `compose.yaml`，由源码构建镜像；本次封版实际运行验收为 Windows 原生进程，最新 Docker 构建和容器启动未重新实测。

## 配置

以下只有变量名和说明，实际凭证不进入交付文档。.env 修改后重启后端；已有进程环境变量优先。

| 变量 | 用途与默认行为 |
| --- | --- |
| LLM_API_BASE | OpenAI 兼容生成接口；MiMo 普通示例 https://api.xiaomimimo.com/v1，Token Plan 使用自己套餐地址 |
| LLM_API_KEY | 本机服务端凭证；交付示例留空 |
| LLM_MODEL | 教学与规划模型；当前 mimo-v2.6-flash |
| LLM_QUIZ_MODEL / LLM_REVIEW_MODEL / VISION_MODEL | 留空复用主模型，当前四类模型相同 |
| LLM_QUIZ_THINKING / LLM_REVIEW_THINKING | 默认 false；MiMo 使用 thinking.type 显式启闭，硅基流动兼容服务使用 enable_thinking |
| LLM_QUIZ_THINKING_BUDGET / LLM_REVIEW_THINKING_BUDGET | 默认 1024，范围 128–32768；支持的服务可接收推理预算，不是费用上限 |
| LLM_JSON_MODE_ENABLED | 默认 true；结构化请求 JSON，特定上游格式运输故障可有界恢复，结果仍严格校验 |
| LLM_MIN_OUTPUT_TOKENS | 默认 0，使用任务预算；范围 0–32768，不等于实际使用量 |
| EMBEDDING_ENABLED / EMBEDDING_MODEL | 单一 MiMo 示例关闭嵌入，使用中文 BM25；可选模型 BAAI/bge-m3 |
| EMBEDDING_API_BASE / EMBEDDING_API_KEY | 可选独立向量服务，启用时成对填写；留空可沿用生成配置，但 MiMo 不提供本项目的 embeddings 接口 |
| VISION_ENABLED | 默认 true；关闭后仍能查看原 PDF/页图 |
| MAX_UPLOAD_MB | 默认 200 MiB，整数 1–512 |
| TUTOR_DATA_DIR | 默认源码根目录下的 data；保存上传文件、索引与学习数据 |
| TUTOR_DATABASE | 可选数据库文件路径；留空时使用 TUTOR_DATA_DIR 下的 tutor.sqlite3 |
| COOKIE_SECURE | 本机 HTTP 为 false，正式 HTTPS 使用 true |
| PROVIDER_TIMEOUT_SECONDS | 默认 90 秒，可按模型服务调整 |

首启公共种子默认本地解析，不自动付费嵌入。开启向量时，上传片段和查询发送给向量服务；问答发送必要问题、历史和证据；视觉发送实际选中页图片。向量失败可降级 BM25，并保留进度。见 [合规说明](docs/compliance.md)。

MiMo 适配显式发送推理开关，流式教学关闭隐藏推理。审核是独立请求，通常仍用同一家模型，不能当作专家终审。回答记录实际教学模型，审核轨迹另记审核模型；usage 累计各阶段返回用量，不含独立嵌入，也不是平台账单。JSON 不能保证语义正确，格式恢复和失败调用也可能消耗额度。

## 测试与复现

本地回归使用临时数据库和受控模型替身，不改用户运行数据，不调用付费教学模型。

~~~powershell
.venv/Scripts/python.exe -m pytest backend/tests -q
.venv/Scripts/python.exe scripts/evaluate_retrieval.py
cd frontend
npm test
npm run build
~~~

最新全量与构建统计见 [最终验收记录](docs/validation/release-verification-20261011.json)。2026-10-11 已用真实 Mimo、内部浏览器、引用弹窗、实际工具和 SQLite 读回，完成三模式 8 个基础场景：总览、生活讲解、递归单步及学生反馈、二分纠错及学生反馈、二分轨迹和分页。正常教学与恢复路径分开记录，见 [逐项结果](docs/validation/starter-prompts-20261011-results.json)。

真实模型脚本会消耗额度，重传整书可能重新嵌入。浏览器验收另需 Playwright，运行应用本身不需要自动化依赖。历史 OS、网络、土木及旧模型原记录只在工作区保留，不分发或迁移为当前模型成绩。

## 常见问题

**模型不可用**：在本机检查接口、凭证和模型是否属于同一服务，重启后端。不要在页面、日志或工单粘贴实际凭证。

**额度或限流**：检查平台额度与限制，稍后重试。内部失败先尝试通用回复；完全断网时只能给学习续接提示，不伪造知识答案。

**生成等待**：教学执行按 PROVIDER_TIMEOUT_SECONDS 限时，默认90秒；通用模型恢复另最多30秒，并不超过该配置。超时转诚实续接，取消会释放当前会话；这不是所有回答固定时长的承诺。

**扫描 PDF 没文字**：先转为有文本层 PDF。整书摄取不含 OCR，单页视觉不代替整书 OCR。损坏/加密文件请重新导出。

**图题与章节错漏**：结构来自标题与文字层，打开原页核对；可补充本人图示与笔记，不增加实测掌握度。

**练习草稿未保存**：只有“草稿已保存”表示服务端确认。冲突时选择“读取已保存作答”或“保存本页作答”；两者不评分，提交后以权威评分记录为准。

**练习来源**：内置 408 是自编题库，自建课程才依据教材生成。来源、盲解与解析检查不能保证所有题自动正确，仍需核对。

**学习记录不见了**：确认原账号和原数据库/数据卷。新演示入口会创建新账号，换空数据目录也会得到新数据，不等于旧记录丢失。

## 数据与边界

中文内置资料为 48 个原创补充章节、48 道自编题；每科另有一部完整开放英文教材，共 1036 物理页。不是商业指定教材、官方考试大纲或历年真题。来源、许可与 SHA256 见 [教材来源](docs/textbook-provenance.md)。

“本人私有”表示应用权限，在线模型仍接收必要上下文。掌握度来自实际作答，是系统估计；没有真实学生成绩提升研究。当前 SQLite 单实例，不含整书 OCR、任意代码执行、复杂图完整拓扑保证或大规模容量验收。

## 目录与文档

~~~text
backend/app/       API、认证、数据、摄取、检索、Agent、学习业务
backend/tests/     正常、异常、边界与权限回归
frontend/src/      工作台、引用、导图、练习、计划
content/           原创和许可教材、来源与检索测试集
docs/              需求、技术、合规、最终验收
docs/demo/         最终公开演示视频、字幕与复现说明
scripts/           启动、种子重建和可复用验证
tools/             可选报告、演示制作与发布文件校验
data/              私有运行数据，本机生成且不提交
deliverables/      本机历史交付归档，不提交到仓库
~~~

- [需求与比赛证据](docs/requirements-evidence.md)
- [项目脚本说明](scripts/README.md) · [可选交付工具](tools/README.md)
- [系统架构](docs/architecture.md) · [技术报告](docs/technical-report.md)
- [数据与合规](docs/compliance.md) · [第三方归属](THIRD_PARTY_NOTICES.md)
- [思维导图指南](docs/mindmap-learning-guide.md) · [当前三模式实测](docs/starter-prompts-20261011.md)
- [答辩要点](docs/defense-and-interview.md) · [项目学习指南](docs/project-study-guide.md)

公开技术依据：[LangGraph 工作流与 Agent](https://docs.langchain.com/oss/python/langgraph/workflows-agents)、[MiMo 对话 API](https://mimo.mi.com/docs/en-US/api/chat)。比赛平台提交和参赛身份由提交者办理。
