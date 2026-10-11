"""Public release file selection and credential checks; no application runtime dependency."""


from __future__ import annotations


import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
DATE = "20261011"
OUT = ROOT / "deliverables"
VIDEO_NAME = f"学思同行-最终演示-{DATE}.mp4"
REPORT_NAME = f"学思同行-项目技术与验收报告-{DATE}"


ROOT_FILES = ["README.md", "THIRD_PARTY_NOTICES.md", "LICENSE", ".env.example",
              ".gitignore", ".dockerignore", "Dockerfile", "compose.yaml"]


DOCS = ["api-contract.md", "architecture.md", "compliance.md", "requirements.md",
        "requirements-evidence.md", "technical-report.md", "submission-report.md",
        "submission-checklist.md", "contest-submission-checklist.md", "test-cases.md",
        "test-cases-final.md", "textbook-provenance.md", "starter-prompts-20261011.md",
        "defense-and-interview.md", "project-study-guide.md", "mindmap-learning-guide.md",
        "mindmap-design.md", "ui-design-guidelines.md"]


EVIDENCE = ["release-verification-20261011.json", "release-requirements-20261011.json",
            "clean-release-runtime-20261011.json", "startup-port-safety-20261011.json",
            "mimo-release-learning-loop-20261011.json", "starter-prompts-20261011-results.json",
            "pytest-release-final-20261011.xml", "pytest-release-export-20261011.xml",
            "pytest-release-deadlines-20261011.xml", "source-archive-reproduction-20261011.json",
            "pytest-release-final-student-20261011.xml",
            "extracted-release-runtime-20261011.json", "ds-mindmap-browser-results.json",
            "mindmap-export-browser-results.json", "ds-account-boundaries-results.json",
            "ds-stream-cancel-results.json",
            "demo-ui-learning-20261011.json",
            "demo-final-verification-20261011.json",
            "report-final-verification-20261011.json",
            "test-case-coverage-20261011.json",
            "compliance-document-verification-20261011.json"]


ASSETS = ["architecture-20261011.png", "teaching-workspace-20261011.png",
          "practice-workspace-20261011.png", "plan-workspace-20261011.png",
          "map-workspace-20261011.png", "socratic-workspace-20261011.png"]


IGNORED = {"node_modules", "__pycache__", ".pytest_cache", ".venv", "data", ".git",
           ".mypy_cache", ".ruff_cache", "playwright-report", "test-results"}


SCRIPTS = ['README.md', 'start.ps1', 'start.sh', 'build_catalog.py', 'evaluate_retrieval.py', 'verify_clean_release.py', 'verify_windows_startup.py', 'verify_mimo_release_loop.py', 'verify_mindmap_export_browser.py', 'verify_ds_practice.py']


TOOLS = ['README.md', 'release_source.py', 'build_submission_report.py', 'assemble_release_demo.py', 'make_release_narration.ps1']


DEMO_FILES = ['README.md', '学思同行-最终演示-20261011.mp4', '学思同行-最终演示-20261011.srt', '学思同行-演示解说与复现-20261011.md']


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def configured_secrets() -> list[bytes]:
    result = []
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            name, value = line.split("=", 1)
            if re.search(r"KEY|TOKEN|SECRET|PASSWORD", name, re.I):
                value = value.strip().strip('"').strip("'")
                if len(value) > 8 and not value.isdigit():
                    result.append(value.encode("utf-8"))
    return result


def check_secrets(raw: bytes, name: str, secrets: list[bytes]) -> None:
    if any(secret in raw for secret in secrets):
        raise RuntimeError(f"拒绝打包：实际配置凭证出现于 {name}")
    if name.lower().endswith((".docx", ".zip")):
        with ZipFile(io.BytesIO(raw)) as nested:
            for info in nested.infolist():
                if not info.is_dir():
                    check_secrets(nested.read(info), f"{name}/{info.filename}", secrets)
    elif name.lower().endswith(".pdf"):
        try:
            import pypdf
        except ImportError as exc:
            raise RuntimeError("打包检查需要 pypdf 解压检查 PDF 正文") from exc
        text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(io.BytesIO(raw)).pages)
        if any(secret in text.encode("utf-8") for secret in secrets):
            raise RuntimeError(f"拒绝打包：实际配置凭证出现于 PDF 正文 {name}")


def selected_sources() -> list[Path]:
    accepted = [ROOT / name for name in ROOT_FILES if (ROOT / name).is_file()]
    for folder in ["backend", "frontend", ".github", "content/408", "content/open-textbooks",
                   "content/evaluation", "content/examples", "docs/reference-licenses"]:
        for path in (ROOT / folder).rglob("*"):
            if not path.is_file() or any(part in IGNORED for part in path.relative_to(ROOT).parts):
                continue
            if path.is_symlink() or path.name.startswith((".env", "~$")):
                continue
            if path.suffix.lower() in {".pyc", ".log", ".sqlite3", ".db", ".tsbuildinfo", ".webm", ".mp4"}:
                continue
            if folder == "backend" and path.suffix.lower() == ".xml":
                continue
            accepted.append(path)
    accepted.append(ROOT / "content/catalog.json")
    accepted.extend(ROOT / "scripts" / name for name in SCRIPTS if (ROOT / "scripts" / name).is_file())
    accepted.extend(ROOT / "tools" / name for name in TOOLS if (ROOT / "tools" / name).is_file())
    accepted.extend(ROOT / "docs/demo" / name for name in DEMO_FILES if (ROOT / "docs/demo" / name).is_file())
    accepted.extend(ROOT / "docs" / name for name in DOCS if (ROOT / "docs" / name).is_file())
    accepted.extend(ROOT / "docs/validation" / name for name in EVIDENCE if (ROOT / "docs/validation" / name).is_file())
    accepted.extend(ROOT / "docs/release-assets" / name for name in ASSETS if (ROOT / "docs/release-assets" / name).is_file())
    for ext in [".docx", ".pdf"]:
        path = ROOT / "docs" / (REPORT_NAME + ext)
        if path.is_file():
            accepted.append(path)
    return sorted(set(accepted))


def public_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    # Synthetic pytest parameter IDs are documentation, not executable fixtures.
    if path.suffix.lower() == ".xml" and "docs/validation" in path.as_posix():
        raw = re.sub(rb"sk-[A-Za-z0-9_-]{20,}", b"synthetic-credential-redacted", raw)
    elif path.name == f"test-case-coverage-{DATE}.json":
        coverage = json.loads(raw)
        for case in coverage["cases"]:
            for proof in case["evidence"]:
                evidence_path = (ROOT / proof["artifact"]).resolve()
                if not evidence_path.is_relative_to(ROOT) or not evidence_path.is_file():
                    raise RuntimeError("用例证据路径缺失或超出项目目录")
                original_sha256 = digest(evidence_path.read_bytes())
                if proof["sha256"] != original_sha256:
                    raise RuntimeError(f"用例证据与原始核对记录不同：{proof['artifact']}")
                distributed_sha256 = digest(public_bytes(evidence_path))
                if distributed_sha256 != original_sha256:
                    proof["tested_original_sha256"] = original_sha256
                    proof["sha256"] = distributed_sha256
                    proof["redaction_note"] = "仅公开XML中的合成凭证参数展示已脱敏；sha256对应包内副本，tested_original_sha256对应原始执行记录，测试与通过结果未改。"
        coverage["distributed_evidence_hash_scope"] = "每条 evidence.sha256 对应本包公开证据文件；仅脱敏文件另保留 tested_original_sha256 和 redaction_note。"
        raw = (json.dumps(coverage, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return raw


def verification_gates() -> tuple[dict, int]:
    media = json.loads((ROOT / f"docs/validation/demo-final-verification-{DATE}.json").read_text(encoding="utf-8-sig"))
    video = ROOT / "docs/demo" / VIDEO_NAME
    facts = media.get("media", {})
    if not (media.get("ready") is True and facts.get("sha256") == digest(video.read_bytes())
            and 180 <= facts.get("duration_seconds", 0) <= 300
            and facts.get("video_codec") == "h264" and facts.get("audio_codec") == "aac"
            and media.get("full_decode_passed") is True):
        raise RuntimeError("拒绝打包：最终演示未通过时长、音视频、哈希及完整解码核验")
    junit = ET.parse(ROOT / f"docs/validation/pytest-release-final-{DATE}.xml").getroot()
    suites = list(junit.iter("testsuite"))
    failed = sum(int(s.get("failures", "0")) + int(s.get("errors", "0")) for s in suites)
    tests = sum(int(s.get("tests", "0")) for s in suites)
    if failed or tests < 2200:
        raise RuntimeError("拒绝打包：最新全量回归未通过")
    for name in ["test-cases-final.md", REPORT_NAME + ".docx", REPORT_NAME + ".pdf", "compliance.md"]:
        if not (ROOT / "docs" / name).is_file():
            raise RuntimeError(f"拒绝打包：缺少必需文档 {name}")
    report = json.loads((ROOT / f"docs/validation/report-final-verification-{DATE}.json").read_text(encoding="utf-8-sig"))
    if (report.get("status") != "passed"
            or report.get("source_sha256") != digest((ROOT / "docs/submission-report.md").read_bytes())
            or len(report.get("artifacts", [])) != 2
            or any(item.get("sha256") != digest((ROOT / item["path"]).read_bytes()) for item in report["artifacts"])):
        raise RuntimeError("拒绝打包：正式技术报告与已完成排版核验的版本不一致")
    coverage = json.loads((ROOT / f"docs/validation/test-case-coverage-{DATE}.json").read_text(encoding="utf-8-sig"))
    if (coverage.get("passed_case_count_with_stated_scope") != 32
            or coverage.get("case_without_executed_evidence_count") != 0
            or coverage.get("competition_requirement", {}).get("category_counts") != {"normal": 17, "abnormal": 8, "boundary": 7}):
        raise RuntimeError("拒绝打包：用例数量、场景覆盖或已执行证据未完成核对")
    compliance = json.loads((ROOT / f"docs/validation/compliance-document-verification-{DATE}.json").read_text(encoding="utf-8-sig"))
    if (compliance.get("checks_passed") is not True
            or compliance.get("document_sha256") != digest((ROOT / "docs/compliance.md").read_bytes())):
        raise RuntimeError("拒绝打包：中文合规说明与核对记录不一致")
    return media, tests
