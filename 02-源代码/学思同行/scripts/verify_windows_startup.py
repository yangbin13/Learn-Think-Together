"""Check Windows PowerShell 5 startup and clean up only the owned process tree."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--occupied-port", type=int, default=8766)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    project = args.project_root.resolve()
    data_dir = args.data_dir.resolve()
    if args.port in {8765, 8766, args.occupied_port} or not 1024 <= args.port <= 65535:
        raise ValueError("Use an independent test port")
    if data_dir in {project, (project / "data").resolve()}:
        raise ValueError("Use an isolated validation data directory")
    data_dir.mkdir(parents=True, exist_ok=True)
    report = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "scope": "Actual Windows PowerShell 5 -File startup with existing configuration, isolated data and owned process tree", "checks": [], "completed": False, "passed": False,
              "original_service_modified": False, "original_database_used": False, "paid_provider_requests": 0}
    env = os.environ.copy()
    env.update({"TUTOR_DATA_DIR": str(data_dir), "TUTOR_DATABASE": str(data_dir / "tutor.sqlite3"), "LLM_API_KEY": "", "LLM_MODEL": "mimo-v2.6-flash", "EMBEDDING_ENABLED": "false", "EMBEDDING_API_KEY": "", "EMBEDDING_API_BASE": "", "COOKIE_SECURE": "false"})
    process = None
    output = (data_dir / "powershell-startup.log").open("ab")

    def shell(command):
        result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, check=True)
        return result.stdout.decode("utf-8", errors="replace").strip()

    def listener(port):
        text = shell(f"@(Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess) | ConvertTo-Json -Compress")
        value = json.loads(text) if text else []
        return value if isinstance(value, list) else [value]

    def descendants(parent):
        command = "$queue = [System.Collections.Generic.Queue[int]]::new(); $queue.Enqueue(" + str(int(parent)) + "); $owned = @(); while ($queue.Count -gt 0) { $parentId = $queue.Dequeue(); foreach ($child in @(Get-CimInstance Win32_Process -Filter ('ParentProcessId=' + $parentId))) { $owned += [int]$child.ProcessId; $queue.Enqueue([int]$child.ProcessId) } }; $owned | ConvertTo-Json -Compress"
        text = shell(command)
        value = json.loads(text) if text else []
        return value if isinstance(value, list) else [value]

    def save():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name, ok, details=None):
        item = {"name": name, "passed": bool(ok), "details": details}
        report["checks"].append(item)
        save()
        print(json.dumps(item, ensure_ascii=False), flush=True)
        if not ok:
            raise AssertionError(name)

    def stop_owned():
        nonlocal process
        if process is None:
            return
        owned = descendants(process.pid) if process.poll() is None else []
        for child in reversed(owned):
            # Stopping the Uvicorn child can make its launcher exit before the
            # next cleanup step; an already absent owned process is settled.
            shell(f"Stop-Process -Id {int(child)} -Force -ErrorAction SilentlyContinue; exit 0")
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=15)
        process = None

    try:
        before = listener(args.occupied_port)
        check("original_service_exists_before_startup_test", bool(before))
        version = shell("$PSVersionTable.PSVersion.ToString()")
        check("tested_actual_windows_powershell_5", version.startswith("5."), {"version": version})
        refused = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(project / "scripts/start.ps1"), "-Port", str(args.occupied_port), "-SkipInstall"], cwd=project, env=env, capture_output=True)
        diagnostic = refused.stdout + refused.stderr
        (data_dir / "occupied-port-diagnostic.txt").write_bytes(diagnostic)
        check("occupied_port_refused_after_successful_script_parsing", refused.returncode != 0 and str(args.occupied_port).encode() in diagnostic and b"ParserError" not in diagnostic)
        check("original_service_process_is_unchanged", listener(args.occupied_port) == before)
        check("independent_test_port_is_free", not listener(args.port))
        process = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(project / "scripts/start.ps1"), "-Port", str(args.port), "-SkipInstall"], cwd=project, env=env, stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        base = f"http://127.0.0.1:{args.port}"
        deadline = time.monotonic() + 180
        with httpx.Client(base_url=base, timeout=3, trust_env=False) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Owned startup script exited; diagnostic remains private")
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise TimeoutError("Startup script did not bring up the owned service")
            live = listener(args.port)
            owned = descendants(process.pid)
            check("script_starts_only_its_owned_uvicorn_process", len(live) == 1 and live[0] in owned)
            check("script_started_api_and_current_frontend_are_accessible", client.get("/").content == (project / "frontend/dist/index.html").read_bytes())
            config = client.get("/api/config/public").json()
            check("script_uses_isolated_no_key_configuration", config["configured"] is False and config["embedding_model"] == "disabled / BM25")
        stop_owned()
        check("all_owned_service_processes_stop_and_port_is_free", not listener(args.port))
        check("original_service_stays_unchanged_after_owned_cleanup", listener(args.occupied_port) == before)
        report["completed"] = True
        report["passed"] = True
    except Exception as error:
        report["failure_type"] = type(error).__name__
        raise
    finally:
        stop_owned()
        output.close()
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["total_checks"] = len(report["checks"])
        report["passed_checks"] = sum(item["passed"] for item in report["checks"])
        save()


if __name__ == "__main__":
    main()
