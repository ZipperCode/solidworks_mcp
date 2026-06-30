from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Final, Sequence

from solidworks_mcp.decrypted_parts_process import (
    SolidWorksCloseResult,
    close_solidworks_processes,
    solidworks_close_succeeded,
    solidworks_process_snapshot,
)
from solidworks_mcp.decrypted_parts_plan import (
    DEFAULT_SOURCE_DIR,
    JsonMap,
    JsonValue,
    PartCase,
    model_plan_for_case,
    part_cases,
)

ROOT: Final = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT: Final = ROOT / "scripts" / "smoke_mounting_plate.py"


@dataclass(frozen=True, slots=True)
class BatchRunOptions:
    output_root: Path
    timeout_seconds: int
    close_existing_processes: bool


@dataclass(frozen=True, slots=True)
class BatchItemResult:
    index: int
    name: str
    document_type: str
    path: str
    plan_file: str
    return_code: int
    status: str
    production_status: str | None
    run_dir: str | None
    report_file: str | None
    failure_count: int
    close_policy: str
    close_before_count: int
    close_after_count: int
    close_target_count: int
    close_remaining_target_count: int
    close_failure_reason: str | None
    stdout_file: str
    stderr_file: str


@dataclass(frozen=True, slots=True)
class SmokeRunRecord:
    plan_file: Path
    stdout_file: Path
    stderr_file: Path
    return_code: int
    summary: JsonMap | None
    close_result: SolidWorksCloseResult


def run_case(
    part_case: PartCase,
    options: BatchRunOptions,
) -> BatchItemResult:
    plans_dir = options.output_root / "plans"
    logs_dir = options.output_root / "logs"
    plans_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plans_dir / f"{part_case.slug}.json"
    stdout_file = logs_dir / f"{part_case.slug}.stdout.txt"
    stderr_file = logs_dir / f"{part_case.slug}.stderr.txt"
    plan_file.write_text(
        json.dumps(model_plan_for_case(part_case), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    env = smoke_environment(options.output_root, f"batch_{part_case.slug}")
    command = [sys.executable, str(SMOKE_SCRIPT), "--plan", str(plan_file), "--summary-only"]
    initial_processes = solidworks_process_snapshot()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=options.timeout_seconds,
            check=False,
        )
        return_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        return_code = 124
        stdout = text_or_empty(exc.stdout)
        stderr = text_or_empty(exc.stderr) + f"\nTimed out after {options.timeout_seconds} seconds."
    except OSError as exc:
        return_code = 1
        stdout = ""
        stderr = str(exc)
    finally:
        close_result = close_solidworks_processes(
            initial_processes,
            close_existing_processes=options.close_existing_processes,
        )
    stdout_file.write_text(stdout, encoding="utf-8")
    stderr_file.write_text(stderr, encoding="utf-8")
    return item_result(
        part_case,
        SmokeRunRecord(
            plan_file=plan_file,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            return_code=return_code,
            summary=smoke_summary(stdout),
            close_result=close_result,
        ),
    )


def smoke_environment(output_root: Path, run_id: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "SOLIDWORKS_MCP_ADAPTER": "solidworks",
            "SOLIDWORKS_MCP_OUTPUT_DIR": str(output_root),
            "SOLIDWORKS_MCP_RUN_ID": run_id,
            "SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN": "1",
            "SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY": "1",
            "SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW": "1",
            "SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT": "1",
        }
    )
    return env


def smoke_summary(stdout: str) -> JsonMap | None:
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        value = json.loads(stdout[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def item_result(
    part_case: PartCase,
    run_record: SmokeRunRecord,
) -> BatchItemResult:
    summary = run_record.summary
    execution = json_map(summary.get("execution")) if summary is not None else {}
    failures = json_list(execution.get("failures"))
    production_status = string_or_none(execution.get("status"))
    close_failure_reason = close_failure_reason_for(run_record.close_result)
    status = "failed" if close_failure_reason is not None else production_status or (
        "failed" if run_record.return_code else "unknown"
    )
    return BatchItemResult(
        index=part_case.index,
        name=part_case.path.name,
        document_type=part_case.document_type,
        path=str(part_case.path),
        plan_file=str(run_record.plan_file),
        return_code=run_record.return_code,
        status=status,
        production_status=production_status,
        run_dir=string_or_none(execution.get("run_dir")),
        report_file=string_or_none(execution.get("report_file")),
        failure_count=len(failures) + (1 if close_failure_reason is not None else 0),
        close_policy=run_record.close_result.policy,
        close_before_count=run_record.close_result.before_count,
        close_after_count=run_record.close_result.after_count,
        close_target_count=run_record.close_result.target_count,
        close_remaining_target_count=run_record.close_result.remaining_target_count,
        close_failure_reason=close_failure_reason,
        stdout_file=str(run_record.stdout_file),
        stderr_file=str(run_record.stderr_file),
    )


def close_failure_reason_for(result: SolidWorksCloseResult) -> str | None:
    if solidworks_close_succeeded(result):
        return None
    return (
        f"SolidWorks close policy {result.policy} failed: "
        f"exit_code={result.exit_code}; remaining_target_count={result.remaining_target_count}."
    )


def json_map(value: JsonValue | None) -> JsonMap:
    return value if isinstance(value, dict) else {}


def json_list(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def string_or_none(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def text_or_empty(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--max-parts", type=int)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--stop-on-first-failure", action="store_true")
    parser.add_argument("--close-existing-solidworks-processes", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = args.output_root or ROOT / "outputs" / f"decrypted_parts_batch_{timestamp}"
    options = BatchRunOptions(
        output_root=output_root,
        timeout_seconds=args.timeout_seconds,
        close_existing_processes=args.close_existing_solidworks_processes,
    )
    results: list[BatchItemResult] = []
    for part_case in part_cases(args.source_dir, start=args.start, max_parts=args.max_parts):
        result = run_case(part_case, options)
        results.append(result)
        write_batch_summary(output_root, results)
        print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
        if args.stop_on_first_failure and result.status != "accepted":
            break
    write_batch_summary(output_root, results)
    return 0 if all(result.status == "accepted" for result in results) else 1


def write_batch_summary(output_root: Path, results: Sequence[BatchItemResult]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "diagnose_runs_command": f"{sys.executable} {ROOT / 'scripts' / 'diagnose_runs.py'} {output_root} --summary-only",
        "run_directory_layout": "canonical_nested_run_dirs_under_batch_root",
        "run_count": len(results),
        "accepted_count": sum(1 for result in results if result.status == "accepted"),
        "rejected_count": sum(1 for result in results if result.status == "rejected"),
        "failed_count": sum(1 for result in results if result.status not in {"accepted", "rejected"}),
        "results": [asdict(result) for result in results],
    }
    (output_root / "batch_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
