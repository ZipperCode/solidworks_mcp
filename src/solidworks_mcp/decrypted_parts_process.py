from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Final, Sequence, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

POWERSHELL_EXE: Final = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


@dataclass(frozen=True, slots=True)
class SolidWorksCloseResult:
    policy: str
    before_count: int
    after_count: int
    target_count: int
    remaining_target_count: int
    exit_code: int
    raw_output: str


@dataclass(frozen=True, slots=True)
class SolidWorksProcessSnapshot:
    process_ids: tuple[int, ...]


def solidworks_process_snapshot() -> SolidWorksProcessSnapshot:
    try:
        completed = subprocess.run(
            [
                str(POWERSHELL_EXE),
                "-NoProfile",
                "-Command",
                "@(Get-Process -Name SLDWORKS -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id) | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return SolidWorksProcessSnapshot(())
    try:
        value = json.loads(completed.stdout.strip() or "[]")
    except json.JSONDecodeError:
        value = []
    if isinstance(value, int):
        return SolidWorksProcessSnapshot((value,))
    if isinstance(value, list):
        process_ids = tuple(int(item) for item in value if isinstance(item, int))
        return SolidWorksProcessSnapshot(process_ids)
    return SolidWorksProcessSnapshot(())


def close_solidworks_processes(
    initial_processes: SolidWorksProcessSnapshot,
    *,
    close_existing_processes: bool,
) -> SolidWorksCloseResult:
    policy = "close_all_sldworks_processes" if close_existing_processes else "close_batch_started_sldworks_processes"
    initial_ids = set(initial_processes.process_ids)
    current = solidworks_process_snapshot()
    target_ids = target_solidworks_process_ids(
        current.process_ids,
        initial_ids,
        close_existing_processes=close_existing_processes,
    )
    if not target_ids:
        after = solidworks_process_snapshot()
        return SolidWorksCloseResult(
            policy=policy,
            before_count=len(current.process_ids),
            after_count=len(after.process_ids),
            target_count=0,
            remaining_target_count=0,
            exit_code=0,
            raw_output="",
        )
    id_list = ",".join(str(process_id) for process_id in target_ids)
    command = (
        f"$targetIds = @({id_list}); "
        "$before = @(Get-Process -Name SLDWORKS -ErrorAction SilentlyContinue); "
        "$targets = @($before | Where-Object { $targetIds -contains $_.Id }); "
        "if ($targets.Count -gt 0) { $targets | Stop-Process -Force; Start-Sleep -Seconds 2 }; "
        "$after = @(Get-Process -Name SLDWORKS -ErrorAction SilentlyContinue); "
        "$remaining = @($after | Where-Object { $targetIds -contains $_.Id }); "
        "[pscustomobject]@{before=$before.Count; after=$after.Count; target=$targets.Count; remainingTarget=$remaining.Count} | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            [str(POWERSHELL_EXE), "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return SolidWorksCloseResult(
            policy=policy,
            before_count=len(current.process_ids),
            after_count=-1,
            target_count=len(target_ids),
            remaining_target_count=len(target_ids),
            exit_code=124,
            raw_output=str(exc),
        )
    except OSError as exc:
        return SolidWorksCloseResult(
            policy=policy,
            before_count=len(current.process_ids),
            after_count=-1,
            target_count=len(target_ids),
            remaining_target_count=len(target_ids),
            exit_code=1,
            raw_output=str(exc),
        )
    value = close_result_payload(completed.stdout)
    return SolidWorksCloseResult(
        policy=policy,
        before_count=int(value.get("before", 0)) if value is not None else 0,
        after_count=int(value.get("after", 0)) if value is not None else -1,
        target_count=int(value.get("target", 0)) if value is not None else len(target_ids),
        remaining_target_count=int(value.get("remainingTarget", len(target_ids))) if value is not None else len(target_ids),
        exit_code=completed.returncode,
        raw_output=completed.stdout.strip(),
    )


def target_solidworks_process_ids(
    current_process_ids: Sequence[int],
    initial_process_ids: set[int],
    *,
    close_existing_processes: bool,
) -> tuple[int, ...]:
    if close_existing_processes:
        return tuple(current_process_ids)
    return tuple(process_id for process_id in current_process_ids if process_id not in initial_process_ids)


def solidworks_close_succeeded(result: SolidWorksCloseResult) -> bool:
    return result.exit_code == 0 and result.remaining_target_count == 0


def close_result_payload(stdout: str) -> dict[str, JsonValue] | None:
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        value = json.loads(stdout[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
