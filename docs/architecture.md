# 学思同行系统架构与调用链

当前技术基线为 React 19、TypeScript、Vite、FastAPI、SQLite WAL 和 LangGraph。教学、规划、审核、出题与页图服务统一使用 Mimo 的 OpenAI 兼容接口；BGE-M3 向量服务独立可选。具体行为以仓库中的当前源码为准。

## 组件

~~~mermaid
flowchart TB
  Student[学生浏览器 React 和 TypeScript] -->|同源 Cookie REST SSE| API[FastAPI]
  API --> Auth[身份 课程所有权 请求校验]
  Auth --> DB[(SQLite WAL 业务状态)]
  API --> Ingest[持久化摄取任务]
  Ingest --> Parse[PDF DOCX TXT MD 解析 分块 章节]
  Parse --> Chunks[(真实片段 来源 物理页)]
  Parse -. 可选 .-> Embed[独立 BGE-M3 向量服务]
  Embed --> Chunks
  API --> Graph[LangGraph 有界教学流程]
  Graph --> Route[任务选择与证据判定]
  Route -->|实际证据足够的基础问题| Teach[任务化教学提示]
  Route -->|需要补证或核验| Planner[工具规划]
  Planner --> Tools[受限教材与计算工具]
  Tools --> Search[BM25 可选向量 RRF]
  Search --> Chunks
  Tools --> Page[精读相邻页 有界页图渲染]
  Page --> Vision[Mimo 单页视觉观察]
  Vision --> Graph
  Tools --> Calc[确定性算法与数值验证]
  Calc --> Graph
  Graph --> Teach
  Teach --> Guard[协议 引用 数值 事实审查]
  Guard -->|批准| Save[最终正文 引用 轨迹保存]
  Save --> DB
  Save --> Student
  Guard -->|内部失败| Recover[一次通用模型回复与安全续接]
  Recover --> Save
  API --> Learning[练习 草稿 评分 错题 计划 导出]
  Learning --> DB
~~~

本地生产仅需一个后端进程，FastAPI 同时托管构建后的前端。默认端口 8765；当前工作区演示 8766。开发时 Vite 使用 5173并代理 /api。

## 模块职责

| 模块 | 实际职责 |
| --- | --- |
| config.py / provider.py | 服务端配置、OpenAI 兼容请求、Mimo 推理开关、JSON 运输恢复、截断检查、安全诊断和用量累计 |
| auth.py / db.py | scrypt 密码、散列会话令牌、资源鉴权、外键、事务、WAL 和持久记录 |
| ingestion.py / knowledge_map.py | 文本摄取、章节树、分块、质量提示、进度与向量批次续建 |
| retrieval.py / material_overview.py | 课程权限过滤、BM25/向量/RRF、真实目录前言选择与结构证据保留 |
| materials.py / figures.py | 原文件、相邻文字页、单页 PDFium 渲染、图题与本人补充图示 |
| agent.py | LangGraph 状态流程、受限工具、历史来源重新鉴权、本轮证据、生成和审查 |
| teaching_route.py / teaching_prompts.py | 以当前任务和真实证据选择调用路径，按模式加载必要教学规则 |
| socratic_reply.py / grounded_reply.py | 单步反馈与问句协议、证据段落和来源 ID、本轮内容校验 |
| citations.py / fact_review.py | 引用规范化、位置与当前状态检查、真实工具断言、局部事实/小问审核 |
| tools.py / calculator.py | 白名单算法和 AST 算术，禁止任意代码执行 |
| chat_recovery.py | 严格流程失败后隔离旧草稿，通用教学、诚实标注和不可用续接 |
| practice.py / quiz_review.py / quiz_evidence.py | 当前教材出题、来源与概念检验、独立盲解、解析核对、批准后保存 |
| main.py | REST/SSE、同源写校验、学习业务、并发/会话锁、健康检查、静态前端 |
| frontend/src/views 与 lib | 教学界面、资料、引用/工具卡、章节导图、草稿、练习和计划 |

具体模块文件以源码为准。接口结构见 api-contract.md，环境与复现命令见 README。

## 教学流程

实际 Graph 注册 planner、tools、evidence_check、teaching 四个节点。首轮处理和教学内部审核不冒充额外 Graph 节点。

1. 检查学生、课程和会话，读取真实历史；历史来源重新验证教材身份、权限和就绪状态。
2. 整书概览先选择有权限的目录/前言；其他问题检索最多 6 个有效正文片段。只有标题或偶然提及不能启用基础概念捷径。
3. 基础任务证据足够时省去额外模型规划；计算、代码、原图、比较、实现边界或额外要求继续按受限工具流程。
4. 默认最多 4 轮工具决策，每轮最多 6 项调用，文字证据最多 18 项、页图最多 2 页。整书结构最多固定保留 6 项，不被后续局部检索挤掉。首轮检索、嵌入、教学和审核不计入工具数，不能把 24 次工具上限当作总请求上限。
5. 生成先缓冲草稿。普通回答按 blocks/text/source_ids 协议绑定实际来源；单步启发按 feedback/question 校验。新手首轮不先给答案，学生尝试先作直接判断，只推进一个任务。学生当前明确要求直接解释或不要继续提问时切换直接教学；资料、引用、否定与假设中的措辞不自动取得该权限。
6. 检查协议、引用、位置、派生数值与工具执行声明，按条件发起独立事实审查。首次明确内容拒绝可重生成一次；批准后返回 done.final_text，使 UI 与数据库一致。
7. 教学执行以 provider_timeout 限时，默认90秒；内部格式、检索、工具、复核失败或整轮超时转独立恢复调用，最多 min(30秒, provider_timeout)。恢复不接收失败草稿、旧引用或内部报错，只提供未核实教材/工具的通用教学，判断学生最后一次当前尝试。仍不可用时生成服务端学习续接提示；402/429明确报错，取消和权限边界保留。

Mimo 通过 thinking.type 明确启闭推理，流式教学关闭隐藏推理；硅基流动兼容服务保留 enable_thinking 适配。reasoning_content 不展示或保存。JSON 特定运输故障可有界改用普通格式要求重试，但本地结构、截断、内容过滤和来源检查不放宽。

## 工具与来源

文字来源、视觉观察、确定性工具是不同证据。教材引用不能证明未运行的数值计算；原页图入口不等于视觉模型已正确识别；一次正确算法轨迹不能替未执行的变体背书。

页文字精读一次最多 3 页。视觉只处理本人可读、已定位或明确指定的实际物理页；最长边 1800 像素、总像素 300 万、编码最多 8 MiB。这些资源上限不证明复杂图拓扑准确。

## 学习状态

草稿保存与提交评分分开，版本冲突由学生选择读取或覆盖。提交事务保证幂等，答案提交前隐藏，已评分页面读权威快照。错题归档不创造正确作答；只有有效概念测量影响掌握度和薄弱点。计划先用实测薄弱点与目标，再按真实章节顺序补充，未测概念保留待测。

摄取进度和已完成向量批次持久保存，重试补缺失项；已完成会话、练习与计划可恢复。运行中任意聊天节点没有跨进程续跑承诺。

## 部署和验证边界

锁文件、单端口启动和 Docker 配置支持复现。当前 SQLite 是单实例设计。自动回归、真实模型语义、UI、媒体、部署各有独立证据，不能相互代替。测试结果不作为源码仓库中的交付附件保存。
