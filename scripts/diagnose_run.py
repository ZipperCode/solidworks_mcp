"""Summarize a SolidWorks MCP debug run directory.

This script performs offline log analysis only.  It does not connect to
SolidWorks, execute a plan or mutate CAD files, so it is safe to run on macOS
after copying a Windows run directory back for diagnosis.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for offline run diagnosis."""

    parser = argparse.ArgumentParser(description="Diagnose a solidworks-mcp run directory.")
    parser.add_argument("run_dir", help="Path to a run_<timestamp>_<id> directory.")
    parser.add_argument(
        "--tail",
        type=int,
        default=12,
        help="Number of final events to include in the summary.",
    )
    return parser.parse_args()


def main() -> int:
    """Read debug artifacts and print a compact diagnosis summary."""

    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    report = _read_json(run_dir / "execution_report.json")
    artifacts = _read_json(run_dir / "artifacts.json")
    environment = _read_json(run_dir / "environment.json")
    events = _read_jsonl(run_dir / "events.jsonl")

    missing_files = _missing_artifacts(artifacts)
    failed_events = [event for event in events if event.get("status") == "failed"]
    step_results = report.get("step_results", []) if report else []
    failed_steps = [step for step in step_results if not step.get("ok", True)]
    diagnostics = report.get("diagnostics", {}) if report else {}

    summary = {
        "run_dir": str(run_dir),
        "ok": report.get("ok") if report else False,
        "plan_name": report.get("plan_name") if report else None,
        "failure_class": report.get("failure_class") if report else "missing_report",
        "message": report.get("message") if report else "execution_report.json is missing or invalid",
        "adapter": report.get("adapter") if report else environment.get("adapter"),
        "debug_level": environment.get("debug_level"),
        "failed_steps": failed_steps,
        "diagnostics": diagnostics,
        "missing_artifacts": missing_files,
        "failed_events": failed_events,
        "last_events": events[-args.tail:],
        "repro_command": report.get("repro_command") if report else None,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if report and not missing_files else 1


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning an empty object when it is unavailable."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL events while skipping malformed lines."""

    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"event": "diagnose.invalid_jsonl", "status": "failed", "raw": line[:200]})
    except Exception:
        return []
    return events


def _missing_artifacts(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    """Return artifact entries whose indexed files or directories are missing."""

    missing: list[dict[str, Any]] = []
    for group_name in ("fixed_files", "output_files", "preview_files", "directories"):
        group = artifacts.get(group_name, {}) if artifacts else {}
        for name, item in group.items():
            if not item.get("exists", False):
                missing.append({
                    "group": group_name,
                    "name": name,
                    "path": item.get("path"),
                })
    return missing


if __name__ == "__main__":
    raise SystemExit(main())

