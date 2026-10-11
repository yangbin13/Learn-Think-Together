# 项目脚本

本目录保留启动、种子资料重建和可复用验证。应用业务在 `backend/app/` 与 `frontend/src/`；后端回归在 `backend/tests/`，前端回归由 `frontend/package.json` 的 `npm test` 调用。

所有命令从项目根目录运行。日常使用只需要启动脚本；验证脚本由维护者按需要运行。

| 文件 | 用途 | 前提与输出 |
| --- | --- | --- |
| start.ps1 / start.sh | 安装锁定依赖、构建前端、启动应用 | 默认本机端口8765，保留已有 `.env`；Windows可用 `-Port`、`-SkipInstall` |
| build_catalog.py | 从项目原创课程资料重建内置目录与题库 | 维护内容时使用；会写 `content/catalog.json`，普通运行不需要重建 |
| evaluate_retrieval.py | 本地BM25的24条固定查询检查 | 使用临时数据库，禁止模型调用；结果写 `data/validation/retrieval-results.json`，不表示一般问题正确率 |
| verify_clean_release.py | 用独立数据目录与独立端口验证真实HTTP流程 | 要求已有前端构建和Python依赖；必须传 `--data-dir`、`--report`；自行启动并结束其测试进程 |
| verify_windows_startup.py | Windows启动、端口占用和持久化验证 | 需一个已监听的 `--occupied-port`；另外指定未占用测试端口与独立数据目录、报告 |
| verify_mimo_release_loop.py | 原创教材出题、作答、错题与计划的真实MiMo验收 | 自建独立体验账号；`--generate`会付费调用模型；当前验收预期为mimo-v2.6-flash；数据库需与目标服务对应，生成后须独立核对题目再 `--finish` |
| verify_mindmap_export_browser.py | 在Chromium验证实际PNG导出、回忆遮罩和失败恢复 | 需要前端依赖、Python Playwright及Chromium；不需要账号或后端服务，默认结果写 `data/validation/` |
| verify_ds_practice.py | pytest使用的两个纯模型来源辅助函数 | 保留原导入路径；旧私人账号浏览器流程已删除，不是独立验收命令 |

## 常用检查

~~~powershell
.venv/Scripts/python.exe -m pytest backend/tests -q
.venv/Scripts/python.exe scripts/evaluate_retrieval.py
cd frontend
npm test
npm run build
~~~

独立HTTP验收示例（先完成前端构建；数据目录必须为空）：

~~~powershell
.venv/Scripts/python.exe scripts/verify_clean_release.py --port 8791 --data-dir data/validation/http-run --report data/validation/http-run.json
~~~

验证MiMo时，用 `--base` 指定本机服务，`--database` 指定该服务实际SQLite，并为每次验收指定独立 `--private-dir` 和 `--report`。`--generate`、`--finish` 的完整操作见[最终用例集](../docs/test-cases-final.md)。报告输出不会默认覆盖 `docs/validation/` 内的封版证据。

报告和视频制作工具在 [tools/](../tools/README.md)，应用运行不需要它们。旧模型诊断、旧录屏、依赖作者本机登录状态的阶段性脚本已移除；历史验收结果保留原执行范围。
