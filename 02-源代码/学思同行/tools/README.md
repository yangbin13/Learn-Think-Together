# 可选交付维护工具

本目录用于维护报告、演示和发布文件。启动应用及运行常规测试无需安装这些工具的额外依赖；已完成的报告和演示在 `docs/` 中，可直接使用。

| 文件 | 用途 | 使用条件 |
| --- | --- | --- |
| build_submission_report.py | 从 `docs/submission-report.md` 生成正式DOCX/PDF | Python需有python-docx、Pillow、ReportLab、pypdf；当前排版使用Windows中文字体。生成后需另做实际渲染检查 |
| make_release_narration.ps1 | 根据原始分镜生成中文旁白 | Windows SAPI中文语音及本人保留的storyboard.json |
| assemble_release_demo.py | 将真实截图、分镜、旁白及字幕合成演示 | Python需有Pillow、NumPy、OpenCV，并提供FFmpeg；原始capture、录制帧和音频在本机，未公开 |
| release_source.py | 发布文件白名单、实际凭证检查及已验收材料一致性核对 | 提供源码选择函数，供发布流程使用；不启动应用，不调用模型，不执行Git推送 |

三个制作工具从项目根目录使用 `python tools/<文件名>.py` 或 `powershell -File tools/<文件名>.ps1` 执行；先阅读各工具参数与输入条件。它们可能重新写入报告或视频，普通体验与部署不需要执行。

旧的多层ZIP提交生成器已删除。当前提交方式是GitHub仓库，入口为[根README](../README.md)；本地最终ZIP只作归档，公开原始许可证继续保留。
