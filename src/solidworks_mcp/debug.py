"""Debug run artifacts and structured event logging.

The SolidWorks side of this project will often be validated on a separate
Windows machine.  This module keeps every run self-contained so a failed run can
be copied back and inspected without the original terminal session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import platform
import sys
from time import perf_counter
from typing import Any
from uuid import uuid4

from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.schemas import ModelPlan, path_to_string, safe_output_name, write_json_file


FAILURE_CLASSES = {
    "schema",
    "connection",
    "template",
    "selection",
    "holewizard",
    "drawing",
    "export",
    "filesystem",
    "unknown",
}


@dataclass(frozen=True)
class DebugRunContext:
    """Paths and identifiers for one execution run."""

    run_id: str
    run_dir: Path
    debug_level: str
    plan_file: Path
    report_file: Path
    events_file: Path
    environment_file: Path
    artifacts_file: Path

    @property
    def exports_dir(self) -> Path:
        """Directory where model and drawing exports should be written."""

        return self.run_dir / "exports"

    @property
    def previews_dir(self) -> Path:
        """Directory where viewport previews should be written."""

        return self.run_dir / "previews"

    @property
    def macros_dir(self) -> Path:
        """Directory where generated macro fallback artifacts should be written."""

        return self.run_dir / "macros"


class EventRecorder:
    """Append-only JSONL writer for execution and COM call events."""

    def __init__(self, context: DebugRunContext) -> None:
        self._context = context
        self._started_at = perf_counter()
        self._home = Path.home()
        self._context.events_file.parent.mkdir(parents=True, exist_ok=True)

    @property
    def context(self) -> DebugRunContext:
        """Return the run context associated with this recorder."""

        return self._context

    def event(
        self,
        name: str,
        status: str,
        details: dict[str, Any] | None = None,
        *,
        level: str = "basic",
        started_at: float | None = None,
    ) -> None:
        """Write one structured event unless the requested level is disabled."""

        if level == "verbose" and self._context.debug_level != "verbose":
            return

        payload = {
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "run_id": self._context.run_id,
            "event": name,
            "status": status,
            "elapsed_ms": round((perf_counter() - self._started_at) * 1000, 3),
            "details": sanitize_for_log(details or {}, self._home, self._context.debug_level),
        }
        if started_at is not None:
            payload["duration_ms"] = round((perf_counter() - started_at) * 1000, 3)

        with self._context.events_file.open("a", encoding="utf-8") as handle:
            handle.write(_json_line(payload))

    def com_call(
        self,
        method: str,
        parameters: dict[str, Any] | None,
        *,
        result: Any = None,
        error: Exception | str | None = None,
        started_at: float | None = None,
    ) -> None:
        """Record a SolidWorks COM call summary in verbose mode."""

        details: dict[str, Any] = {
            "method": method,
            "parameters": parameters or {},
        }
        if error is not None:
            details["error"] = str(error)
            status = "failed"
        else:
            details["result"] = summarize_value(result)
            status = "completed"

        self.event("com.call", status, details, level="verbose", started_at=started_at)


def create_debug_run_context(config: SolidWorksMCPConfig, plan: ModelPlan) -> DebugRunContext:
    """Create a unique run directory and its standard subdirectories."""

    run_id = config.run_id or f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    run_dir = config.output_root / safe_output_name(plan.name) / f"run_{run_id}"
    for directory in (run_dir, run_dir / "exports", run_dir / "previews", run_dir / "macros"):
        directory.mkdir(parents=True, exist_ok=True)

    return DebugRunContext(
        run_id=run_id,
        run_dir=run_dir,
        debug_level=config.debug_level,
        plan_file=run_dir / "plan.normalized.json",
        report_file=run_dir / "execution_report.json",
        events_file=run_dir / "events.jsonl",
        environment_file=run_dir / "environment.json",
        artifacts_file=run_dir / "artifacts.json",
    )


def write_environment_snapshot(
    context: DebugRunContext,
    config: SolidWorksMCPConfig,
    adapter_name: str,
    *,
    extra: dict[str, Any] | None = None,
) -> str:
    """Write non-sensitive runtime details for later failure analysis."""

    payload = {
        "run_id": context.run_id,
        "adapter": adapter_name,
        "debug_level": context.debug_level,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "python_executable": _redact_home(sys.executable),
        },
        "paths": {
            "output_root": _redact_home(config.output_root),
            "run_dir": _redact_home(context.run_dir),
            "part_template": _path_state(config.part_template),
            "drawing_template": _path_state(config.drawing_template),
        },
        "env": {
            "SOLIDWORKS_MCP_ADAPTER": config.adapter,
            "SOLIDWORKS_MCP_DEBUG_LEVEL": config.debug_level,
            "SOLIDWORKS_MCP_VISIBLE": config.visible,
            "SOLIDWORKS_MCP_MACRO_FALLBACK": config.macro_fallback_enabled,
            "SOLIDWORKS_MCP_RUN_ID_SET": bool(config.run_id),
        },
        "extra": sanitize_for_log(extra or {}, Path.home(), context.debug_level),
    }
    return write_json_file(context.environment_file, payload)


def write_artifacts_index(context: DebugRunContext, report_payload: dict[str, Any]) -> str:
    """Write an index of expected debug and CAD artifacts with existence flags."""

    fixed_files = {
        "plan": context.plan_file,
        "report": context.report_file,
        "events": context.events_file,
        "environment": context.environment_file,
        "artifacts": context.artifacts_file,
    }
    output_files = report_payload.get("output_files", {}) or {}
    preview_files = report_payload.get("preview_files", {}) or {}
    payload = {
        "run_id": context.run_id,
        "run_dir": path_to_string(context.run_dir),
        "fixed_files": _path_map_with_exists(fixed_files),
        "output_files": _path_map_with_exists(output_files),
        "preview_files": _path_map_with_exists(preview_files),
        "directories": _path_map_with_exists(
            {
                "exports": context.exports_dir,
                "previews": context.previews_dir,
                "macros": context.macros_dir,
            }
        ),
    }
    artifact_path = write_json_file(context.artifacts_file, payload)
    payload["fixed_files"]["artifacts"]["exists"] = True
    return write_json_file(context.artifacts_file, payload)


def classify_failure(message: str | None, step_results: list[Any] | tuple[Any, ...] = ()) -> str:
    """Classify a failure using lightweight source and keyword rules."""

    text_parts = [message or ""]
    for result in step_results:
        if hasattr(result, "message"):
            text_parts.append(str(result.message))
        if hasattr(result, "op"):
            text_parts.append(str(result.op))
        if hasattr(result, "details"):
            text_parts.append(str(result.details))
    text = " ".join(text_parts).lower()

    if "validation" in text or "plan." in text or "operations[" in text:
        return "schema"
    if "connect" in text or "dispatch" in text or "sldworks.application" in text:
        return "connection"
    if "template" in text or ".prtdot" in text or ".drwdot" in text:
        return "template"
    if "select" in text or "selection" in text or "selectbyray" in text:
        return "selection"
    if "holewizard" in text or "thread" in text:
        return "holewizard"
    if "drawing" in text or "drawview" in text or "callout" in text:
        return "drawing"
    if "saveas" in text or "export" in text or "sldprt" in text or "step" in text or "stl" in text:
        return "export"
    if "file" in text or "path" in text or "permission" in text or "directory" in text:
        return "filesystem"
    return "unknown"


def repro_command_for_plan(plan_file: Path) -> str:
    """Return a stable smoke command that can rerun the normalized plan."""

    return f"python scripts/smoke_mounting_plate.py --plan {path_to_string(plan_file)}"


def sanitize_for_log(value: Any, home: Path | None = None, debug_level: str = "basic") -> Any:
    """Return a JSON-safe, lightly redacted value for reports and events."""

    home = home or Path.home()
    max_length = 2000 if debug_level == "verbose" else 500
    if isinstance(value, Path):
        return _redact_home(value, home)
    if isinstance(value, str):
        redacted = _redact_home(value, home)
        return redacted if len(redacted) <= max_length else f"{redacted[:max_length]}...[truncated]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {
            str(key): sanitize_for_log(item, home, debug_level)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_log(item, home, debug_level) for item in list(value)[:100]]
    return summarize_value(value)


def summarize_value(value: Any) -> Any:
    """Summarize values that may include non-serializable COM objects."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return path_to_string(value)
    return {
        "type": type(value).__name__,
        "repr": str(value)[:200],
    }


def _json_line(payload: dict[str, Any]) -> str:
    """Serialize one JSONL event line with deterministic separators."""

    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"


def _redact_home(value: str | Path, home: Path | None = None) -> str:
    """Replace the current user home directory in paths and strings."""

    text = str(value)
    home_text = str(home or Path.home())
    return text.replace(home_text, "$HOME")


def _path_state(value: str | Path | None) -> dict[str, Any]:
    """Return a non-sensitive path existence summary."""

    if value is None:
        return {"configured": False, "path": None, "exists": False}
    path = Path(value).expanduser()
    return {
        "configured": True,
        "path": _redact_home(path),
        "exists": path.exists(),
    }


def _path_map_with_exists(values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return path strings with existence flags for artifact indexes."""

    result: dict[str, dict[str, Any]] = {}
    for key, value in values.items():
        path = Path(value)
        result[str(key)] = {
            "path": path_to_string(path),
            "exists": path.exists(),
        }
    return result
