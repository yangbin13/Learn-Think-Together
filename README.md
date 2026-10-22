# 学思同行 ThinkMate

基于教材证据的专业课学习助手。学生可以使用内置的 408 参考课程，也可以创建课程并上传自己有权使用的资料；系统把教材检索、对话、练习和学习记录放在同一工作台中。

## 功能

- 讲解、启发和解题三种教学模式，并根据学生当前反馈继续对话。
- 按课程检索教材片段、显示引用位置，并打开对应原文。
- 管理个人课程和教材，记录练习、错题、复习计划与学习进度。
- 生成课程知识地图；在受限范围内使用计算和算法工具核对结果。
- 通过后端连接可配置的 OpenAI 兼容模型服务；模型凭证不发送到浏览器。

项目使用 React、TypeScript、Vite、FastAPI、LangGraph 和 SQLite。内置课程资料与开放教材保留在 `content/`，相关许可和第三方声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 [教材来源说明](docs/textbook-provenance.md)。

## 环境要求

- Python 3.12 或更新版本
- Node.js 22 或更新版本
- 首次安装依赖及使用在线模型时需要网络

## 快速启动

克隆仓库后，在项目根目录执行。

### Windows PowerShell

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

在根目录的 `.env` 中填写自己的 `LLM_API_KEY`。使用示例中的 MiMo 接口时，`LLM_API_BASE` 和 `LLM_MODEL` 可保持默认值。然后启动：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

### Linux

```sh
test -f .env || cp .env.example .env
# 在 .env 中填写 LLM_API_KEY
sh scripts/start.sh
```

启动后打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。启动脚本会安装锁定依赖、构建前端并启动服务。Windows 可用 `-SkipInstall` 跳过已完成的安装与构建，也可用 `-Port 8766` 指定端口。按 Ctrl+C 停止本次服务。

未填写有效模型凭证时，仍可使用本地课程、资料和学习记录；在线模型功能需要有效凭证。

## Docker

在项目根目录准备好 `.env` 后运行：

```sh
docker compose up --build -d
docker compose logs -f tutor
docker compose down
```

默认服务地址为本机 8765 端口，学习数据保存在 Docker 命名卷中。停止服务时不要添加 `-v`，否则会一并删除该卷。

## 手动安装与开发

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.lock.txt
cd frontend
npm ci
npm run build
cd ..
.venv/Scripts/python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
```

开发前端时，在另一个终端进入 `frontend/` 并运行 `npm run dev`；Vite 开发服务器使用 5173 端口，并将 `/api` 请求代理到后端。

## 测试与构建

```powershell
.venv/Scripts/python.exe -m pytest backend/tests -q
cd frontend
npm test
npm run build
```

Linux/macOS 可将 Python 可执行文件替换为 `.venv/bin/python`。测试使用隔离数据和受控模型替身，不需要真实模型凭证。

## 配置与数据

常用设置见 [.env.example](.env.example)。`LLM_API_KEY` 是服务端凭证；使用其他模型平台时，按服务商设置 `LLM_API_BASE` 和 `LLM_MODEL`。向量检索可选配置独立嵌入服务；未开启时使用本地 BM25 检索。

默认运行数据写入 `data/`，包括 SQLite 数据库和用户上传资料，不应提交到源码仓库。启用在线模型后，服务会发送回答所需的问题、对话上下文和检索片段；启用视觉阅读时也会发送选中的页面图片。需要完全离线处理的资料不应上传到在线模型服务。

## 项目结构

```text
backend/app/       FastAPI 接口、Agent、检索、资料处理和学习业务
backend/tests/     后端回归测试
frontend/src/      React 学习工作台
content/           内置课程资料、开放教材、许可及检索测试数据
scripts/           启动、内容构建和可复用验证脚本
tools/             可选的维护与本地发布工具
docs/              架构、API、界面设计及教材来源说明
.github/           GitHub Actions 工作流
```

源码仓库保留运行与测试所需的代码、配置模板、课程样例、测试夹具和许可信息。报告、演示视频、截图、验收结果及本地运行数据不属于源码仓库内容。
