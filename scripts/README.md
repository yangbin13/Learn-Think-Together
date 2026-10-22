# 项目脚本

所有命令从项目根目录运行。日常体验使用 `start.ps1` 或 `start.sh`；其他脚本供开发与维护时按需调用。

| 文件 | 用途 |
| --- | --- |
| `start.ps1` / `start.sh` | 安装锁定依赖、构建前端并启动应用 |
| `build_catalog.py` | 从原创课程资料重建内置课程目录与题库 |
| `evaluate_retrieval.py` | 使用固定查询检查本地 BM25 检索；不调用模型 |
| `verify_*.py` | 针对启动、HTTP 流程、练习和导图导出的专项验证 |

验证脚本可能需要独立数据目录、模型凭证、浏览器或本机服务。先查看对应脚本的 `--help`，并把报告和临时数据写入 `data/` 等本机目录。生成的结果不会作为交付材料提交到源码仓库。

## 常用命令

```powershell
.venv/Scripts/python.exe -m pytest backend/tests -q
.venv/Scripts/python.exe scripts/evaluate_retrieval.py
cd frontend
npm test
npm run build
```
