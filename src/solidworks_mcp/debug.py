"""Debug run artifacts and structured event logging.

The SolidWorks side of this project will often be validated on a separate
Windows machine.  This module keeps every run self-contained so a failed run can
be copied back and inspected without the original terminal session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
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
    delivery_manifest_file: Path

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
        delivery_manifest_file=run_dir / "delivery_manifest.json",
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
            "SOLIDWORKS_MCP_DISABLE_MACRO_EXECUTION": config.macro_execution_disabled,
            "SOLIDWORKS_MCP_FORCE_HOLEWIZARD_FAILURE": config.force_holewizard_failure,
            "SOLIDWORKS_MCP_FORCE_DRAWING_CALLOUT_FAILURE": config.force_drawing_callout_failure,
            "SOLIDWORKS_MCP_FORCE_DRAWING_DIMENSION_FAILURE": config.force_drawing_dimension_failure,
            "SOLIDWORKS_MCP_FORCE_CAD_CONTENT_FAILURE": config.force_cad_content_failure,
            "SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE": config.force_cleanup_failure,
            "SOLIDWORKS_MCP_FORCE_MATERIAL_FAILURE": config.force_material_failure,
            "SOLIDWORKS_MCP_FORCE_PREFLIGHT_FAILURE": config.force_preflight_failure,
            "SOLIDWORKS_MCP_FORCE_EXPORT_FAILURE": config.force_export_failure,
            "SOLIDWORKS_MCP_FORCE_MODEL_GEOMETRY_FAILURE": config.force_model_geometry_failure,
            "SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW": config.enforce_trusted_workflow,
            "SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT": config.require_direct_hole_callout,
            "SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN": config.close_documents_after_run,
            "SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY": config.cleanup_attach_only,
            "SOLIDWORKS_MCP_ENABLE_CONSTRUCTION_REFERENCE_DIMENSIONS": (
                config.enable_construction_reference_dimensions
            ),
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
        "delivery_manifest": context.delivery_manifest_file,
    }
    output_files = report_payload.get("output_files", {}) or {}
    preview_files = report_payload.get("preview_files", {}) or {}
    payload = {
        "schema_version": "2026-06-06.2",
        "run_id": context.run_id,
        "run_dir": path_to_string(context.run_dir),
        "fixed_files": _path_map_with_exists(fixed_files, include_hash=True, base_dir=context.run_dir),
        "output_files": _path_map_with_exists(output_files, include_hash=True, base_dir=context.run_dir),
        "preview_files": _path_map_with_exists(preview_files, include_hash=True, base_dir=context.run_dir),
        "directories": _path_map_with_exists(
            {
                "exports": context.exports_dir,
                "previews": context.previews_dir,
                "macros": context.macros_dir,
            },
            base_dir=context.run_dir,
        ),
    }
    write_json_file(context.artifacts_file, payload)
    payload["fixed_files"]["artifacts"] = _path_entry(context.artifacts_file, base_dir=context.run_dir)
    return write_json_file(context.artifacts_file, payload)


def write_delivery_manifest(context: DebugRunContext, report_payload: dict[str, Any]) -> str:
    """Write a compact production delivery manifest for downstream clients."""

    artifacts = _read_json_file(context.artifacts_file)
    diagnose_command = f"python scripts/diagnose_run.py {path_to_string(context.run_dir)} --summary-only"
    payload = {
        "schema_version": "2026-06-06.2",
        "run_id": context.run_id,
        "run_dir": path_to_string(context.run_dir),
        "plan_name": report_payload.get("plan_name"),
        "adapter": report_payload.get("adapter"),
        "ok": report_payload.get("ok"),
        "production_verdict": report_payload.get("production_verdict"),
        "report_file": report_payload.get("report_file") or path_to_string(context.report_file),
        "artifacts_file": report_payload.get("artifacts_file") or path_to_string(context.artifacts_file),
        "delivery_manifest_file": path_to_string(context.delivery_manifest_file),
        "events_file": report_payload.get("events_file") or path_to_string(context.events_file),
        "environment_file": report_payload.get("environment_file") or path_to_string(context.environment_file),
        "output_files": artifacts.get("output_files", {}),
        "preview_files": artifacts.get("preview_files", {}),
        "diagnose_command": diagnose_command,
    }
    payload["handoff_summary"] = _delivery_handoff_summary(report_payload, artifacts, diagnose_command)
    return write_json_file(context.delivery_manifest_file, payload)


def _delivery_handoff_summary(
    report_payload: dict[str, Any],
    artifacts: dict[str, Any],
    diagnose_command: str,
) -> dict[str, Any]:
    """Return a one-screen production handoff summary copied into the manifest."""

    verdict = report_payload.get("production_verdict")
    verdict = verdict if isinstance(verdict, dict) else {}
    summary = verdict.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    output_files = artifacts.get("output_files", {})
    output_files = output_files if isinstance(output_files, dict) else {}
    preview_files = artifacts.get("preview_files", {})
    preview_files = preview_files if isinstance(preview_files, dict) else {}
    return {
        "delivery_status": verdict.get("status"),
        "delivery_ok": verdict.get("ok"),
        "production_failures": verdict.get("failures", []),
        "repair_actions": verdict.get("repair_actions", []),
        "run_id": report_payload.get("run_id"),
        "plan_name": report_payload.get("plan_name"),
        "adapter": report_payload.get("adapter"),
        "key_statuses": {
            "trusted_workflow_status": summary.get("trusted_workflow_status"),
            "thread_model_status": summary.get("thread_model_status"),
            "corner_radius_status": summary.get("corner_radius_status"),
            "drawing_view_status": summary.get("drawing_view_status"),
            "drawing_annotation_status": summary.get("drawing_annotation_status"),
            "dimension_layout_status": summary.get("dimension_layout_status"),
            "model_geometry_status": summary.get("model_geometry_status"),
            "mass_property_status": summary.get("mass_property_status"),
            "artifact_validation_status": summary.get("artifact_validation_status"),
            "artifact_content_status": summary.get("artifact_content_status"),
            "cleanup_status": summary.get("cleanup_status"),
            "cleanup_verification_status": summary.get("cleanup_verification_status"),
            "document_state_audit_status": summary.get("document_state_audit_status"),
            "document_state_after_cleanup_run_created_open_count": summary.get(
                "document_state_after_cleanup_run_created_open_count"
            ),
        },
        "artifact_counts": {
            "outputs": len(output_files),
            "previews": len(preview_files),
        },
        "outputs": _manifest_file_list(output_files),
        "previews": _manifest_file_list(preview_files),
        "diagnose_command": diagnose_command,
        "repro_command": report_payload.get("repro_command"),
    }


def _manifest_file_list(files: dict[str, Any]) -> list[dict[str, Any]]:
    """Return stable compact file entries for handoff review."""

    entries: list[dict[str, Any]] = []
    for name in sorted(files):
        item = files.get(name)
        item = item if isinstance(item, dict) else {}
        entries.append(
            {
                "id": name,
                "path": item.get("path"),
                "relative_path": item.get("relative_path"),
                "sha256": item.get("sha256"),
                "size_bytes": item.get("size_bytes"),
                "ok": item.get("ok"),
            }
        )
    return entries


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


def _read_json_file(path: Path) -> dict[str, Any]:
    """Read one JSON file for manifest generation."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


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


def _path_map_with_exists(
    values: dict[str, Any],
    *,
    include_hash: bool = False,
    base_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return path strings with existence and size flags for artifact indexes."""

    result: dict[str, dict[str, Any]] = {}
    for key, value in values.items():
        result[str(key)] = _path_entry(Path(value), include_hash=include_hash, base_dir=base_dir)
    return result


def _path_entry(path: Path, *, include_hash: bool = False, base_dir: Path | None = None) -> dict[str, Any]:
    """Return one artifact path summary with non-empty file validation."""

    exists = path.exists()
    is_file = path.is_file() if exists else False
    is_dir = path.is_dir() if exists else False
    size_bytes = path.stat().st_size if is_file else None
    entry = {
        "path": path_to_string(path),
        "exists": exists,
        "is_file": is_file,
        "is_dir": is_dir,
        "size_bytes": size_bytes,
        "ok": exists and (is_dir or (is_file and size_bytes is not None and size_bytes > 0)),
    }
    relative_path = _relative_path_string(path, base_dir)
    if relative_path is not None:
        entry["relative_path"] = relative_path
    if include_hash and is_file and size_bytes is not None and size_bytes > 0:
        entry["sha256"] = _sha256_file(path)
    return entry


def _relative_path_string(path: Path, base_dir: Path | None) -> str | None:
    """Return a portable path relative to the run directory when possible."""

    if base_dir is None:
        return None
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except Exception:
        return None


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for one artifact file."""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
