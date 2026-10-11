# 学思同行提交材料目录与检查清单

> 当前 GitHub 仓库根目录按五类材料组织，入口见[仓库总 README](../../../README.md)。本清单中的路径以 `02-源代码/学思同行/` 源码项目目录为基准；下方的源码内部材料和验收记录继续保留，外层分类入口见根 README。

核对日期：2026-10-11（Asia/Shanghai）。项目选择中兴赛道命题二“专业课助教智能体”，以 [Learn-Think-Together GitHub 仓库](https://github.com/yangbin13/Learn-Think-Together)提交作品。根目录 [README.md](../README.md) 是运行和材料入口；本清单按仓库实际目录定位六项材料。报名、平台提交和组委会审核由参赛者办理，当前实现与验证范围见[技术与验收报告](submission-report.md)。

## 1. 六项必需材料

依据工作区《智能体创新实践大赛参赛手册.docx》P46—P51。P编号为DOCX XML正文段落定位，完整要求见[赛事交付核对](contest-submission-checklist.md)与[需求证据](requirements-evidence.md)。

| 手册要求 | 仓库路径 | 具体内容与复现入口 |
| --- | --- | --- |
| 可运行作品及演示模式 | backend/、frontend/、scripts/、Dockerfile、compose.yaml | 当前项目、前端源码及依赖锁文件、Windows/Linux启动脚本、Docker配置；启动脚本安装依赖并构建前端，再点击“体验学习工作台”创建独立体验账号，无固定测试密码 |
| 技术文档 | docs/学思同行-项目技术与验收报告-20261011.docx/.pdf | 按系统架构图、模块说明、核心功能介绍、技术路线、部署方式、依赖环境、配置项说明七章组织，另附验收结果；Markdown源为 [submission-report.md](submission-report.md) |
| README | README.md | 安装、环境、配置、启动、运行、测试和FAQ，以及六项材料入口；凭证由使用者在本机填写 |
| 至少10条正常、异常和边界用例 | docs/test-cases-final.md、docs/validation/ | [最终用例](test-cases-final.md)共32条：正常17、异常8、边界7；附每条实际结果、验证方式与[证据映射](validation/test-case-coverage-20261011.json) |
| 3—5分钟实际演示视频 | docs/demo/学思同行-最终演示-20261011.mp4 | 展示原创内置408资料的实际操作，4分31秒中文解说；同目录有 .srt 字幕及演示复现说明 |
| 合规说明 | docs/compliance.md | 一个中文[数据来源与隐私合规说明](compliance.md)，覆盖四项规定并合并必要署名；原始许可在 docs/reference-licenses/ 中，私人教材、账号库、Cookie与实际密钥不上传 |

当前交付入口为 GitHub 仓库链接，不要求另交 ZIP。以上路径均相对于源码项目根目录；技术报告、用例、演示和合规材料放在 docs/ 中，与可运行源码一起提交。不使用旧视频或仅重命名旧源码代替当前材料。

## 2. 如何运行

1. 执行 `git clone https://github.com/yangbin13/Learn-Think-Together.git`，再进入 `Learn-Think-Together/02-源代码/学思同行/` 源码项目根目录。
2. 按[README](../README.md)准备Python 3.12+、Node.js 22+和依赖；首次安装及在线模型需要网络。
3. 将 `.env.example` 复制为源码项目根目录 `.env`，已有配置保留。使用仓库示例的普通 MiMo 地址与模型时，只需在本机填写 `LLM_API_KEY`；不同平台或 Token Plan 再核对 `LLM_API_BASE`、`LLM_MODEL`。示例的凭证字段留空，EMBEDDING_ENABLED=false 使用BM25。
4. Windows执行 `powershell -ExecutionPolicy Bypass -File scripts/start.ps1`；默认地址 `http://127.0.0.1:8765`。已安装和构建时可用 `-SkipInstall`，指定其他空闲端口可用 `-Port`。
5. 使用独立体验账号。内置课程、参考资料、练习及无凭证续接可体验；在线问答和教材出题需要有效模型服务。
6. 按最终用例复现学生提问、三种模式、引用与工具、作答、错题、计划、刷新和导出。

没有提供公网部署链接。本轮Windows原生与实际解压项目的运行检查使用已安装依赖；不宣称新机联网安装或最新Docker/Linux环境已实测。对应范围见 `validation/release-verification-20261011.json`、`validation/extracted-release-runtime-20261011.json`。

## 3. 仓库提交核对

- 提交当前业务源码、测试、前端源码及依赖锁文件与项目文档，frontend/dist 不上传；当前141个冻结文件的SHA与验收版本一致，仓库内容与排除项由提交前审计核对。此前已从实际验证ZIP解压目录检查启动和学习业务，该记录是历史复现证据，不表示 GitHub 克隆后另跑了一次验收。
- 32条用例均在各自所列方式与范围有已执行通过的证据；真实模型、真实HTTP、受控自动及历史浏览器记录分别说明，不统称32条最后版本浏览器全量复测。
- 代码全量回归、前端检查与构建、PowerShell启动安全、真实Mimo场景、清洁数据运行及媒体检查分别列出，不合并成回答正确率。
- DOCX保存后复开并以WPS实际渲染逐页查看；PDF独立生成后以Poppler渲染逐页查看。文档结果见 `validation/report-final-verification-20261011.json`。
- `.env`、SQLite、用户上传、私人教材原文与截图、Cookie、实际API凭证、日志、缓存、`.venv`、`node_modules`和旧交付目录不上传；本地Git元数据不是提交文件。
- 当前正式视频采用原创内置408内容，真实模型失败轮不剪辑或描述为成功。之前私人完整教材验收仅公开必要摘要。
- 开放教材保留原PDF、作者、许可、来源及哈希；预构建前端保留对应第三方许可。

冻结代码后的结果和仓库提交审计优先于工作区历史记录。只有当次通过的场景计入当次通过范围，历史失败与未验证项目不自动变成当前通过。

比赛不要求额外提交 SHA256 清单或单独的英文第三方合规文档。当前中文合规说明为 docs/compliance.md；源码原始版权和许可文本继续保留。

## 4. 报名与实际提交

个人报名材料与公开作品仓库分开。报名材料如含真实身份信息，仅按组委会要求通过报名渠道提交，不上传公开仓库；具体信息由参赛者确认，不编造身份字段。

参赛者按当期通知核对报名是否已提交、准确截止时间、提交平台、队伍成员、命名/大小限制及可能新增的签字材料。手册P80规定具体赛程以组委会通知为准；GitHub 仓库提交不代表已完成比赛平台登记或审核。

额外学习材料见[项目学习指南](project-study-guide.md)、[答辩与面试](defense-and-interview.md)和[思维导图使用](mindmap-learning-guide.md)。这些材料方便理解与复现，不增加赛事硬性提交项。
