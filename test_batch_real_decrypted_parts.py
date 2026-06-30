from __future__ import annotations

import json
from pathlib import Path

from solidworks_mcp import decrypted_parts_batch as batch
from solidworks_mcp.decrypted_parts_batch import (
    BatchRunOptions,
    SmokeRunRecord,
    item_result,
    run_case,
)
from solidworks_mcp.decrypted_parts_process import (
    SolidWorksCloseResult,
    SolidWorksProcessSnapshot,
    target_solidworks_process_ids,
)
from solidworks_mcp.decrypted_parts_plan import PartCase, model_plan_for_case, slug_for_part


def test_model_plan_for_part_uses_manufacturing_drawing_contract() -> None:
    source_path = Path(r"C:\models\330ZH顶边框(解密).SLDPRT")
    part_case = PartCase(1, source_path, slug_for_part(1, source_path), "part")

    plan = model_plan_for_case(part_case)

    assert plan["output_formats"] == ["sldprt", "step", "slddrw", "pdf", "dwg"]
    drawing_profile = plan["drawing_profile"]
    assert isinstance(drawing_profile, dict)
    assert drawing_profile["view_style"] == "manufacturing_rotational"
    import_op = plan["operations"][0]
    assert isinstance(import_op, dict)
    parameters = import_op["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["document_type"] == "part"
    json.dumps(plan, ensure_ascii=False)


def test_model_plan_for_assembly_uses_assembly_drawing_contract() -> None:
    source_path = Path(r"C:\models\SND-330ZH总装配体(解密).SLDASM")
    part_case = PartCase(2, source_path, slug_for_part(2, source_path), "assembly")

    plan = model_plan_for_case(part_case)

    assert plan["output_formats"] == ["sldasm", "step", "slddrw", "pdf", "dwg"]
    drawing_profile = plan["drawing_profile"]
    assert isinstance(drawing_profile, dict)
    assert drawing_profile["view_style"] == "assembly_general"
    import_op = plan["operations"][0]
    assert isinstance(import_op, dict)
    parameters = import_op["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["document_type"] == "assembly"
    json.dumps(plan, ensure_ascii=False)


def test_item_result_extracts_smoke_verdict_and_close_counts(tmp_path: Path) -> None:
    source_path = Path(r"C:\models\part.SLDPRT")
    part_case = PartCase(3, source_path, slug_for_part(3, source_path), "part")
    summary = {
        "execution": {
            "status": "rejected",
            "failures": ["basic_dimensions_created"],
            "run_dir": r"C:\runs\run_001",
            "report_file": r"C:\runs\run_001\execution_report.json",
        }
    }

    result = item_result(
        part_case,
        SmokeRunRecord(
            plan_file=tmp_path / "plan.json",
            stdout_file=tmp_path / "stdout.txt",
            stderr_file=tmp_path / "stderr.txt",
            return_code=1,
            summary=summary,
            close_result=SolidWorksCloseResult(
                policy="close_batch_started_sldworks_processes",
                before_count=1,
                after_count=0,
                target_count=1,
                remaining_target_count=0,
                exit_code=0,
                raw_output="{}",
            ),
        ),
    )

    assert result.status == "rejected"
    assert result.failure_count == 1
    assert result.close_policy == "close_batch_started_sldworks_processes"
    assert result.close_before_count == 1
    assert result.close_after_count == 0
    assert result.close_target_count == 1
    assert result.close_remaining_target_count == 0
    assert result.close_failure_reason is None


def test_item_result_marks_failed_when_solidworks_close_fails(tmp_path: Path) -> None:
    source_path = Path(r"C:\models\part.SLDPRT")
    part_case = PartCase(4, source_path, slug_for_part(4, source_path), "part")
    summary = {"execution": {"status": "accepted", "failures": []}}

    result = item_result(
        part_case,
        SmokeRunRecord(
            plan_file=tmp_path / "plan.json",
            stdout_file=tmp_path / "stdout.txt",
            stderr_file=tmp_path / "stderr.txt",
            return_code=0,
            summary=summary,
            close_result=SolidWorksCloseResult(
                policy="close_batch_started_sldworks_processes",
                before_count=1,
                after_count=1,
                target_count=1,
                remaining_target_count=1,
                exit_code=0,
                raw_output="{}",
            ),
        ),
    )

    assert result.status == "failed"
    assert result.failure_count == 1
    assert result.close_failure_reason is not None


def test_target_solidworks_process_ids_preserves_preexisting_processes_by_default() -> None:
    target_ids = target_solidworks_process_ids(
        (10, 11),
        {10},
        close_existing_processes=False,
    )

    assert target_ids == (11,)


def test_target_solidworks_process_ids_can_close_existing_processes_when_requested() -> None:
    target_ids = target_solidworks_process_ids(
        (10, 11),
        {10},
        close_existing_processes=True,
    )

    assert target_ids == (10, 11)


def test_run_case_records_smoke_process_start_failure(monkeypatch, tmp_path: Path) -> None:
    source_path = Path(r"C:\models\part.SLDPRT")
    part_case = PartCase(5, source_path, slug_for_part(5, source_path), "part")
    close_calls: list[bool] = []

    def fail_run(*_args, **_kwargs) -> None:
        raise OSError("spawn failed")

    def fake_snapshot() -> SolidWorksProcessSnapshot:
        return SolidWorksProcessSnapshot((123,))

    def fake_close(
        _initial_processes: SolidWorksProcessSnapshot,
        *,
        close_existing_processes: bool,
    ) -> SolidWorksCloseResult:
        close_calls.append(close_existing_processes)
        return SolidWorksCloseResult(
            policy="close_batch_started_sldworks_processes",
            before_count=1,
            after_count=0,
            target_count=1,
            remaining_target_count=0,
            exit_code=0,
            raw_output="{}",
        )

    monkeypatch.setattr(batch.subprocess, "run", fail_run)
    monkeypatch.setattr(batch, "solidworks_process_snapshot", fake_snapshot)
    monkeypatch.setattr(batch, "close_solidworks_processes", fake_close)

    result = run_case(
        part_case,
        BatchRunOptions(
            output_root=tmp_path,
            timeout_seconds=10,
            close_existing_processes=False,
        ),
    )

    assert result.status == "failed"
    assert result.return_code == 1
    assert close_calls == [False]
    assert Path(result.stderr_file).read_text(encoding="utf-8") == "spawn failed"
