from __future__ import annotations

import pytest
import solidworks_mcp.adapters.solidworks as solidworks_adapter
from solidworks_mcp.executor import _build_existing_model_production_acceptance_result, _trusted_workflow_result
from solidworks_mcp.run_diagnostics import _trusted_dimension_evidence_ok
from solidworks_mcp.adapters.solidworks import (
    SolidWorksCOMAdapter,
    _best_existing_model_fillet_arc_edge,
    _best_existing_model_hole_circle_edge,
    _best_existing_model_hole_position_edges,
    _existing_model_bbox_sketch_dimension_specs,
    _existing_model_geometry_profile,
    _existing_model_overall_dimension_specs,
    _existing_model_prismatic_note_dimension_items,
)
from solidworks_mcp.schemas import DrawingProfile, ModelOperation, ModelPlan, PlanValidationError


class FakeDrawingWithModelAnnotations:
    def InsertModelAnnotations3(self, *_args) -> None:
        return None


class FakeCurveEdge:
    def __init__(self, params: tuple[float, ...]) -> None:
        self.params = params

    def GetCurveParams2(self) -> tuple[float, ...]:
        return self.params


class FakeConstructionSegment:
    def __init__(self) -> None:
        self.construction_geometry = False
        self.selected: list[bool] = []

    @property
    def ConstructionGeometry(self) -> bool:
        return self.construction_geometry

    @ConstructionGeometry.setter
    def ConstructionGeometry(self, value: bool) -> None:
        self.construction_geometry = value

    def Select2(self, append: bool, _mark: int) -> bool:
        self.selected.append(append)
        return True


class FakeDisplayDimension:
    def GetAnnotation(self) -> object:
        return object()


class FakeConstructionSketchManager:
    def InsertSketch(self, _update_edit_rebuild: bool) -> bool:
        raise AssertionError("construction dimensions must not open a drawing sketch")


class FakeConstructionDrawing:
    def __init__(self) -> None:
        self.SketchManager = FakeConstructionSketchManager()
        self.lines: list[tuple[float, float, float, float, float, float]] = []

    def CreateLine2(self, x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> FakeConstructionSegment:
        self.lines.append((x1, y1, z1, x2, y2, z2))
        return FakeConstructionSegment()


class FakeRecoverableConstructionDrawing:
    def CreateLine2(self, *_args: float) -> None:
        raise RuntimeError("recoverable line creation failure")


class FakeDebugRecorder:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def event(
        self,
        name: str,
        status: str,
        details: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> None:
        self.events.append({"name": name, "status": status, "details": details or {}})

    def com_call(self, *_args: object, **_kwargs: object) -> None:
        return None


def test_existing_model_plan_rejects_non_isolated_source_import(tmp_path) -> None:
    source = tmp_path / "source.SLDPRT"
    source.write_text("fake", encoding="utf-8")

    with pytest.raises(PlanValidationError, match="copy_to_run_dir must be true"):
        ModelPlan.from_dict(
            {
                "name": "unsafe_existing_model_import",
                "units": "mm",
                "output_formats": ["sldprt", "pdf", "dwg"],
                "operations": [
                    {
                        "op": "import_existing_model",
                        "parameters": {
                            "path": str(source),
                            "document_type": "part",
                            "copy_to_run_dir": False,
                        },
                    },
                    {"op": "make_drawing", "parameters": {}},
                ],
                "drawing_profile": {
                    "enabled": True,
                    "view_style": "manufacturing_rotational",
                    "projection": "first_angle",
                    "export_formats": ["pdf", "dwg"],
                },
            }
        )


def test_trusted_existing_model_workflow_rejects_direct_non_isolated_import() -> None:
    plan = ModelPlan(
        name="unsafe_existing_model_import",
        units="mm",
        output_formats=("sldprt", "pdf", "dwg"),
        operations=(
            ModelOperation(
                op="import_existing_model",
                parameters={
                    "path": "source.SLDPRT",
                    "document_type": "part",
                    "copy_to_run_dir": False,
                },
            ),
            ModelOperation(op="make_drawing", parameters={}),
        ),
        drawing_profile=DrawingProfile(
            view_style="manufacturing_rotational",
            projection="first_angle",
            export_formats=("pdf", "dwg"),
        ),
    )

    result = _trusted_workflow_result(plan)

    assert result["ok"] is False
    assert result["status"] == "unsupported_workflow"
    assert result["copy_to_run_dir"] is False
    assert "copy_to_run_dir=true" in result["failure_reason"]


def test_existing_model_geometry_profile_treats_thin_near_square_plate_as_prismatic() -> None:
    profile = _existing_model_geometry_profile({"x": 0.016005, "y": 0.757005, "z": 0.819005})

    assert profile["kind"] == "prismatic"
    assert profile["draft_classification"] == "imported_prismatic_machining_draft"
    assert profile["reason"] == "thin_sheet_like_bbox"


def test_existing_model_geometry_profile_keeps_long_cylinder_rotational() -> None:
    profile = _existing_model_geometry_profile({"x": 0.150, "y": 0.150, "z": 0.700})

    assert profile["kind"] == "rotational"
    assert profile["draft_classification"] == "imported_rotational_machining_draft"


def test_insert_model_annotations_does_not_satisfy_required_prismatic_dimensions() -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    adapter._drawing_view_handles = {}
    adapter._warnings = []
    adapter.record_com_call = lambda *_args, **_kwargs: None
    events = []
    adapter.record_event = lambda name, status, payload: events.append((name, status, payload))
    view_result = {
        "layout": {
            "layout_style": "manufacturing_rotational",
            "existing_model_geometry_profile": {"kind": "prismatic"},
        }
    }
    result = {"attempts": []}

    final = adapter._try_insert_existing_model_overall_dimensions(
        FakeDrawingWithModelAnnotations(),
        None,
        view_result,
        ["overall_length", "overall_width"],
        result,
    )

    assert final["import_model_dimensions_result"]["status"] == "completed_unverified"
    assert final["import_model_dimensions_result"]["trusted_for_required_dimensions"] is False
    assert final["status"] == "dimension_creation_failed"
    assert final["missing_dimensions"] == ["overall_length", "overall_width"]
    stage_events = [
        (status, payload["stage"])
        for name, status, payload in events
        if name == "drawing.stage"
    ]
    assert ("started", "import_model_dimensions") in stage_events
    assert ("completed", "import_model_dimensions") in stage_events
    assert ("completed", "manual_dimension_specs") in stage_events
    assert ("skipped", "construction_dimension_specs") in stage_events
    assert final["construction_dimension_fallback_status"] == "skipped_hang_guard"


def test_prismatic_overall_length_tracks_longest_section_axis() -> None:
    view_result = {
        "layout": {
            "existing_model_geometry_profile": {"kind": "prismatic"},
            "model_dimensions_m": {"x": 0.23700505, "y": 0.75700505, "z": 0.01600505},
        },
        "views": [
            {"role": "hole_face", "outline": [0.040000, 0.060835, 0.190000, 0.278165]},
            {"role": "section", "outline": [0.112339, 0.060835, 0.128563, 0.278165]},
            {"role": "end", "outline": [0.227945, 0.198317, 0.377911, 0.219523]},
            {"role": "flat_pattern", "outline": [0.227945, 0.206627, 0.377911, 0.227833]},
        ],
    }

    specs = _existing_model_overall_dimension_specs({}, view_result)
    overall_length = next(spec for spec in specs if spec["id"] == "overall_length")
    overall_height_specs = [spec for spec in specs if spec["id"] == "overall_height"]
    hole_diameter_specs = [spec for spec in specs if spec["id"] == "hole_diameter"]

    assert overall_length["view_role"] == "hole_face"
    assert overall_length["method"] == "AddVerticalDimension2"
    assert overall_length["edge_selector_data"]["axis"] == "y"
    assert overall_length["edge_selector_data"]["expected_length_m"] == 0.75700505
    assert overall_height_specs[0]["view_role"] == "end"
    assert overall_height_specs[0]["method"] == "AddVerticalDimension2"
    assert overall_height_specs[0]["edge_selector_data"]["axis"] == "z"
    assert overall_height_specs[0]["edge_selector_data"]["expected_length_m"] == 0.01600505
    assert [spec["view_role"] for spec in hole_diameter_specs] == ["hole_face", "flat_pattern", "end", "section"]
    assert all(spec["minimum_selections"] == 1 for spec in hole_diameter_specs)


def test_prismatic_manual_overall_specs_follow_dynamic_axis_mapping_for_all_thin_axes() -> None:
    cases = [
        (
            {"x": 0.006, "y": 0.086, "z": 0.030},
            {"overall_length": "y", "overall_width": "z", "overall_height": "x"},
        ),
        (
            {"x": 0.086, "y": 0.006, "z": 0.030},
            {"overall_length": "x", "overall_width": "z", "overall_height": "y"},
        ),
        (
            {"x": 0.086, "y": 0.030, "z": 0.006},
            {"overall_length": "x", "overall_width": "y", "overall_height": "z"},
        ),
    ]
    for dimensions, expected_axes in cases:
        view_result = {
            "layout": {
                "existing_model_geometry_profile": {"kind": "prismatic"},
                "model_dimensions_m": dimensions,
            },
            "views": [
                {"role": "hole_face", "outline": [0.04, 0.06, 0.19, 0.28]},
                {"role": "section", "outline": [0.11, 0.06, 0.13, 0.28]},
                {"role": "end", "outline": [0.23, 0.20, 0.38, 0.22]},
            ],
        }

        specs = _existing_model_overall_dimension_specs({}, view_result)
        axes_by_id = {
            str(spec["id"]): str(spec["edge_selector_data"]["axis"])
            for spec in specs
            if spec["id"] in {"overall_length", "overall_width", "overall_height"}
        }

        assert axes_by_id == expected_axes


def test_prismatic_manufacturing_layout_adds_hole_face_view_without_changing_gate_style(monkeypatch) -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    adapter._drawing = object()
    adapter._require_model = lambda: object()
    monkeypatch.setattr(solidworks_adapter, "_drawing_sheet_size_m", lambda _drawing, _profile: (0.42, 0.297))
    monkeypatch.setattr(
        solidworks_adapter,
        "_read_model_bounding_box",
        lambda _model: {
            "status": "read",
            "bbox_m": [-0.043, 0.0, -0.003, 0.043, 0.03, 0.003],
        },
    )

    layout = adapter._build_existing_model_manufacturing_layout(
        ModelPlan(name="prismatic_handle", units="mm", operations=()),
        DrawingProfile(view_style="manufacturing_rotational", projection="first_angle"),
    )

    assert layout["layout_style"] == "manufacturing_rotational"
    assert layout["layout_substyle"] == "manufacturing_prismatic_hole_face"
    assert layout["hole_face_view_names"][:2] == ["*Top", "*上视"]
    assert layout["slots"]["hole_face"]["width_m"] == 0.086
    assert layout["slots"]["hole_face"]["height_m"] == 0.03
    assert layout["scale"] == 1.0


def test_prismatic_bbox_sketch_specs_use_hole_face_axes_and_thickness() -> None:
    view_result = {
        "layout": {
            "scale": 1.0,
            "existing_model_geometry_profile": {"kind": "prismatic"},
            "model_dimensions_m": {"x": 0.237, "y": 0.757, "z": 0.016},
            "hole_face_dimensions": {
                "width_m": 0.237,
                "height_m": 0.757,
                "face_axes": ["x", "y"],
                "thin_axis": "z",
            },
            "slots": {
                "hole_face": {"x": 0.12, "y": 0.16, "width_m": 0.237, "height_m": 0.757},
                "end": {"x": 0.32, "y": 0.22, "width_m": 0.237, "height_m": 0.237},
            },
        }
    }

    specs = _existing_model_bbox_sketch_dimension_specs(view_result)
    specs_by_id = {spec["id"]: spec for spec in specs}

    assert list(specs_by_id) == ["overall_width", "overall_length", "overall_height"]
    assert specs_by_id["overall_length"]["view_role"] == "hole_face"
    assert specs_by_id["overall_length"]["method"] == "AddVerticalDimension2"
    assert specs_by_id["overall_width"]["view_role"] == "hole_face"
    assert specs_by_id["overall_width"]["method"] == "AddHorizontalDimension2"
    assert specs_by_id["overall_height"]["view_role"] == "end"
    assert specs_by_id["overall_height"]["method"] == "AddVerticalDimension2"
    assert all(spec["scale_is_trusted"] is True for spec in specs)


def test_existing_model_construction_dimensions_use_create_line2_without_opening_sketch() -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    adapter.record_com_call = lambda *_args, **_kwargs: None
    events = []
    adapter.record_event = lambda name, status, payload: events.append((name, status, payload))
    adapter._clear_drawing_selection = lambda: None
    adapter._add_basic_dimension = lambda _drawing, _spec, _attempt: FakeDisplayDimension()
    drawing = FakeConstructionDrawing()
    spec = {
        "id": "overall_length",
        "method": "AddHorizontalDimension2",
        "scale_is_trusted": True,
        "lines": [
            {"start": [0.10, 0.20], "end": [0.10, 0.21]},
            {"start": [0.18, 0.20], "end": [0.18, 0.21]},
        ],
        "points": [{"x": 0.10, "y": 0.21}, {"x": 0.18, "y": 0.21}],
        "position": {"x": 0.14, "y": 0.19},
    }

    result = adapter._try_create_existing_model_construction_dimension(drawing, spec)

    assert result["created"] is True
    assert result["line_count"] == 2
    assert result["selected_count"] == 2
    assert result["line_creation_methods"] == ["ModelDoc2.CreateLine2", "ModelDoc2.CreateLine2"]
    assert len(drawing.lines) == 2
    assert ("drawing.stage", "started", {"stage": "construction_line_create", "id": "overall_length", "index": 0, "method": "ModelDoc2.CreateLine2"}) in events


def test_generate_drawing_records_stage_events_for_existing_model(tmp_path) -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    events = []
    profile = DrawingProfile(view_style="manufacturing_rotational", projection="first_angle")
    plan = ModelPlan(
        name="stage_event_part",
        units="mm",
        operations=(
            ModelOperation(
                op="import_existing_model",
                parameters={
                    "path": str(tmp_path / "source.SLDPRT"),
                    "document_type": "part",
                    "copy_to_run_dir": True,
                },
            ),
        ),
        drawing_profile=profile,
    )
    fake_drawing = object()
    part_path = tmp_path / "source.SLDPRT"

    adapter._warnings = []
    adapter.record_event = lambda name, status, payload: events.append((name, status, payload))
    adapter._require_sw = lambda: object()
    adapter._require_workspace = lambda: tmp_path
    adapter._new_drawing_document = lambda _sw, _profile: fake_drawing
    adapter._document_title = lambda _drawing: "stage_event_part.slddrw"
    adapter._ensure_part_saved = lambda _plan: part_path
    adapter._create_existing_model_manufacturing_drawing_views = lambda _path, _plan, _profile: {
        "status": "created",
        "views": [{"role": "hole_face"}, {"role": "section"}],
    }
    adapter._try_insert_basic_dimensions = lambda _plan, _view_result, _profile: {
        "status": "basic_dimensions_created",
        "created_dimension_count": 3,
        "missing_dimensions": [],
        "overall_dimension_spec_count": 4,
        "construction_dimension_spec_count": 3,
    }
    adapter._try_insert_metadata_note = lambda _plan: {"status": "metadata_note_created"}
    adapter._try_insert_existing_model_manufacturing_note = (
        lambda _plan, _view_result, _dimension_result, _profile: {"status": "manufacturing_note_created"}
    )
    adapter._try_insert_drawing_recipe_note = (
        lambda _recipe_contract, _view_result: {"status": "recipe_note_created"}
    )
    adapter._save_as = lambda _drawing, path: path.write_text("drawing", encoding="utf-8")

    outputs = adapter.generate_drawing(plan, profile)

    stage_events = [
        (status, payload["stage"])
        for name, status, payload in events
        if name == "drawing.stage"
    ]
    assert stage_events == [
        ("started", "new_drawing"),
        ("completed", "new_drawing"),
        ("started", "ensure_part_saved"),
        ("completed", "ensure_part_saved"),
        ("started", "create_views"),
        ("completed", "create_views"),
        ("started", "insert_basic_dimensions"),
        ("completed", "insert_basic_dimensions"),
        ("started", "hole_callout"),
        ("skipped", "hole_callout"),
        ("started", "metadata_note"),
        ("completed", "metadata_note"),
        ("started", "manufacturing_note"),
        ("completed", "manufacturing_note"),
        ("started", "recipe_note"),
        ("completed", "recipe_note"),
        ("started", "save_drawing"),
        ("completed", "save_drawing"),
        ("started", "write_drawing_manifest"),
        ("completed", "write_drawing_manifest"),
    ]
    create_views_event = next(
        payload
        for name, status, payload in events
        if name == "drawing.stage" and status == "completed" and payload["stage"] == "create_views"
    )
    dimension_event = next(
        payload
        for name, status, payload in events
        if name == "drawing.stage" and status == "completed" and payload["stage"] == "insert_basic_dimensions"
    )
    assert create_views_event["existing_model_type"] == "part"
    assert create_views_event["view_roles"] == ["hole_face", "section"]
    assert dimension_event["construction_dimension_spec_count"] == 3
    assert (tmp_path / "exports" / "stage_event_part.drawing.json").exists()
    assert outputs["drawing_manifest"].endswith("stage_event_part.drawing.json")


def test_existing_model_dimensions_skip_fallback_specs_after_primary_success(monkeypatch) -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    adapter._drawing_view_handles = {}
    adapter.record_com_call = lambda *_args, **_kwargs: None
    adapter.record_event = lambda *_args, **_kwargs: None
    calls = []

    def fake_specs(_views, _view_result):
        return [
            {"id": "overall_height", "method": "primary"},
            {"id": "overall_height", "method": "fallback"},
        ]

    def fake_try_create(_drawing, spec):
        calls.append(spec["method"])
        return {"created": True, "method": spec["method"], "is_display_dimension": True}

    monkeypatch.setattr(solidworks_adapter, "_existing_model_overall_dimension_specs", fake_specs)
    adapter._try_create_basic_dimension_from_spec = fake_try_create
    view_result = {"layout": {"existing_model_geometry_profile": {"kind": "rotational"}}}
    result = {"attempts": []}

    final = adapter._try_insert_existing_model_overall_dimensions(
        FakeDrawingWithModelAnnotations(),
        None,
        view_result,
        ["overall_height"],
        result,
    )

    assert calls == ["primary"]
    assert final["missing_dimensions"] == []


def test_prismatic_dimensions_use_bbox_readback_notes_for_overall_size_when_hole_display_dimensions_exist(
    monkeypatch,
) -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    adapter._drawing_view_handles = {}
    adapter._warnings = []
    adapter.record_com_call = lambda *_args, **_kwargs: None
    adapter.record_event = lambda *_args, **_kwargs: None
    view_result = {
        "layout": {
            "layout_style": "manufacturing_rotational",
            "existing_model_geometry_profile": {"kind": "prismatic"},
            "model_dimensions_mm": {"x": 237.005, "y": 757.005, "z": 16.005},
        },
        "manufacturing_draft": {"classification": "imported_prismatic_machining_draft"},
    }
    required_dimensions = solidworks_adapter._existing_model_dimension_ids_from_view_result(view_result)
    result = {"attempts": [], "required_dimensions": required_dimensions}
    manual_specs = [
        {"id": "overall_length", "method": "AddVerticalDimension2"},
        {"id": "overall_width", "method": "AddHorizontalDimension2"},
        {"id": "overall_height", "method": "AddVerticalDimension2"},
        {"id": "hole_position_x", "method": "AddHorizontalDimension2"},
        {"id": "hole_position_y", "method": "AddVerticalDimension2"},
        {"id": "hole_diameter", "method": "AddDimension2"},
        {"id": "chamfer_radius", "method": "AddDimension2"},
    ]

    def fake_create(_drawing, spec):
        dimension_id = str(spec["id"])
        return {
            "created": dimension_id in {"hole_position_x", "hole_position_y", "hole_diameter"},
            "method": spec["method"],
            "is_display_dimension": True,
        }

    monkeypatch.setattr(solidworks_adapter, "_existing_model_overall_dimension_specs", lambda _views, _result: manual_specs)
    adapter._try_create_basic_dimension_from_spec = fake_create

    final = adapter._try_insert_existing_model_overall_dimensions(
        FakeDrawingWithModelAnnotations(),
        None,
        view_result,
        required_dimensions,
        result,
    )
    by_id = {item["id"]: item for item in final["created_dimensions"]}

    assert "chamfer_radius" not in required_dimensions
    assert final["status"] == "basic_dimensions_created"
    assert final["dimension_layout_status"] == "existing_model_manufacturing_dimensions_created"
    assert final["missing_dimensions"] == []
    assert final["display_dimension_count"] == 3
    assert final["geometry_verified_dimension_count"] == len(required_dimensions)
    for dimension_id in ("overall_length", "overall_width", "overall_height"):
        assert by_id[dimension_id]["classification"] == "geometry_readback_note"
        assert by_id[dimension_id]["annotation_kind"] == "imported_prismatic_overall_size_note"
        assert by_id[dimension_id]["is_display_dimension"] is False
    for dimension_id in ("hole_position_x", "hole_position_y", "hole_diameter"):
        assert by_id[dimension_id]["classification"] == "geometry_verified_dimension"
        assert by_id[dimension_id]["is_display_dimension"] is True


def test_prismatic_readback_notes_map_overall_dimensions_to_actual_model_axes() -> None:
    view_result = {
        "layout": {
            "model_dimensions_mm": {"x": 86.0, "y": 30.0, "z": 6.0},
        }
    }

    notes = _existing_model_prismatic_note_dimension_items(
        ["overall_length", "overall_width", "overall_height"],
        view_result,
    )
    by_id = {item["id"]: item for item in notes}

    assert by_id["overall_length"]["axis"] == "x"
    assert by_id["overall_length"]["value_mm"] == 86.0
    assert by_id["overall_width"]["axis"] == "y"
    assert by_id["overall_width"]["value_mm"] == 30.0
    assert by_id["overall_height"]["axis"] == "z"
    assert by_id["overall_height"]["value_mm"] == 6.0


def test_prismatic_acceptance_allows_bbox_overall_notes_with_hole_display_dimensions() -> None:
    diagnostics = {
        "preflight_status": "ready",
        "existing_model_result": {
            "status": "existing_model_imported",
            "copied_to_run_dir": True,
            "source_path": "source.SLDPRT",
            "run_model_path": "run/source.SLDPRT",
            "document_type": "part",
        },
        "drawing_view_status": "created",
        "drawing_view_result": {
            "status": "created",
            "views": [{"role": "section"}, {"role": "end"}, {"role": "isometric"}, {"role": "hole_face"}],
            "layout": {
                "status": "layout_verified",
                "layout_style": "manufacturing_rotational",
                "projection": "first_angle",
                "clipped_view_count": 0,
                "scale": 1.0,
            },
            "manufacturing_draft": {
                "status": "existing_model_manufacturing_draft_created",
                "classification": "imported_prismatic_machining_draft",
                "rotational_axis": {"status": "not_required"},
                "section_view": {"status": "section_view_created", "created": True},
                "centerline": {"status": "not_required"},
                "center_mark": {"status": "not_required"},
            },
        },
        "drawing_dimension_status": "basic_dimensions_created",
        "drawing_dimension_result": {
            "status": "basic_dimensions_created",
            "required_dimensions": [
                "overall_length",
                "overall_width",
                "overall_height",
                "hole_position_x",
                "hole_position_y",
                "hole_diameter",
            ],
            "created_dimensions": [
                {
                    "id": "overall_length",
                    "method": "model_bbox_readback_note",
                    "is_display_dimension": False,
                    "classification": "geometry_readback_note",
                    "annotation_kind": "imported_prismatic_overall_size_note",
                    "proxy_dimension": False,
                    "axis": "x",
                    "value_mm": 86.0,
                },
                {
                    "id": "overall_width",
                    "method": "model_bbox_readback_note",
                    "is_display_dimension": False,
                    "classification": "geometry_readback_note",
                    "annotation_kind": "imported_prismatic_overall_size_note",
                    "proxy_dimension": False,
                    "axis": "y",
                    "value_mm": 30.0,
                },
                {
                    "id": "overall_height",
                    "method": "model_bbox_readback_note",
                    "is_display_dimension": False,
                    "classification": "geometry_readback_note",
                    "annotation_kind": "imported_prismatic_overall_size_note",
                    "proxy_dimension": False,
                    "axis": "z",
                    "value_mm": 6.0,
                },
                {
                    "id": "hole_position_x",
                    "method": "AddHorizontalDimension2",
                    "is_display_dimension": True,
                    "classification": "geometry_verified_dimension",
                    "proxy_dimension": False,
                },
                {
                    "id": "hole_position_y",
                    "method": "AddVerticalDimension2",
                    "is_display_dimension": True,
                    "classification": "geometry_verified_dimension",
                    "proxy_dimension": False,
                },
                {
                    "id": "hole_diameter",
                    "method": "AddDimension2",
                    "is_display_dimension": True,
                    "classification": "geometry_verified_dimension",
                    "proxy_dimension": False,
                },
            ],
            "created_dimension_count": 6,
            "missing_dimensions": [],
            "display_dimension_count": 3,
            "dimension_layout_status": "existing_model_manufacturing_dimensions_created",
            "geometry_verified_dimension_count": 6,
        },
        "drawing_metadata_note_result": {
            "status": "manufacturing_note_created",
            "manufacturing_note": {
                "status": "manufacturing_note_created",
                "text": (
                    "本图基于导入三维模型自动生成。\n"
                    "未注明尺寸由导入三维模型几何读取，仅供审图/加工前确认。\n"
                    "未注公差、材料、表面处理按人工补充文件或订单要求执行。\n"
                    "关键尺寸/公差需人工确认后方可生产放行。"
                ),
            },
        },
        "artifact_validation_result": {"ok": True, "status": "artifacts_ready"},
        "artifact_content_result": {
            "ok": True,
            "status": "content_ready",
            "cad_content_result": {"ok": True, "status": "cad_artifacts_verified"},
            "pdf_semantic_content_result": {"ok": True, "status": "pdf_semantic_content_verified"},
        },
        "cleanup_result": {"enabled": True, "status": "completed", "cleanup_verification_status": "verified"},
        "custom_property_result": {"status": "not_requested"},
        "material_result": {"status": "not_requested"},
        "model_geometry_status": "geometry_verified",
        "model_geometry_result": {
            "status": "geometry_verified",
            "body_count": 1,
            "measured_dimensions_mm": {"x": 86.0, "y": 30.0, "z": 6.0},
        },
        "mass_property_status": "mass_properties_verified",
        "mass_property_result": {"status": "mass_properties_verified", "mass_kg": 1.0, "volume_m3": 0.001},
        "export_result": {"status": "completed", "failed": []},
        "document_state_audit_result": {
            "status": "verified_no_run_documents_open",
            "after_cleanup_run_created_open_count": 0,
        },
    }
    plan = ModelPlan(
        name="imported_prismatic_part",
        units="mm",
        output_formats=("pdf", "dwg"),
        operations=(
            ModelOperation(
                op="import_existing_model",
                parameters={"path": "source.SLDPRT", "copy_to_run_dir": True, "document_type": "part"},
            ),
            ModelOperation(op="make_drawing", parameters={}),
        ),
        drawing_profile=DrawingProfile(
            view_style="manufacturing_rotational",
            projection="first_angle",
            export_formats=("pdf", "dwg"),
        ),
    )

    assert _trusted_dimension_evidence_ok(diagnostics) is True
    verdict = _build_existing_model_production_acceptance_result(
        plan,
        True,
        diagnostics,
        {"sldprt": "model.SLDPRT", "slddrw": "drawing.SLDDRW", "pdf": "drawing.pdf", "dwg": "drawing.dwg"},
        {"section": "section.png", "end": "end.png", "isometric": "isometric.png"},
        {"ok": True, "status": "accepted", "document_type": "part"},
    )

    assert verdict["ok"] is True
    assert verdict["summary"]["display_dimension_count"] == 3
    assert verdict["summary"]["geometry_verified_dimension_count"] == 6


def test_prismatic_trusted_dimension_evidence_rejects_readback_note_axis_mismatch() -> None:
    diagnostics = {
        "drawing_dimension_status": "basic_dimensions_created",
        "drawing_view_result": {
            "manufacturing_draft": {"classification": "imported_prismatic_machining_draft"},
        },
        "model_geometry_result": {
            "status": "geometry_verified",
            "measured_dimensions_mm": {"x": 86.0, "y": 30.0, "z": 6.0},
        },
        "drawing_dimension_result": {
            "status": "basic_dimensions_created",
            "dimension_layout_status": "existing_model_manufacturing_dimensions_created",
            "required_dimensions": ["overall_length"],
            "created_dimension_count": 1,
            "missing_dimensions": [],
            "display_dimension_count": 0,
            "geometry_verified_dimension_count": 1,
            "created_dimensions": [
                {
                    "id": "overall_length",
                    "method": "model_bbox_readback_note",
                    "classification": "geometry_readback_note",
                    "annotation_kind": "imported_prismatic_overall_size_note",
                    "is_display_dimension": False,
                    "proxy_dimension": False,
                    "axis": "y",
                    "value_mm": 30.0,
                }
            ],
        },
    }

    assert _trusted_dimension_evidence_ok(diagnostics) is False


def test_prismatic_acceptance_rejects_non_display_geometry_verified_overall_dimension() -> None:
    diagnostics = {
        "drawing_dimension_status": "basic_dimensions_created",
        "drawing_view_result": {
            "manufacturing_draft": {"classification": "imported_prismatic_machining_draft"},
        },
        "model_geometry_result": {
            "status": "geometry_verified",
            "measured_dimensions_mm": {"x": 86.0, "y": 30.0, "z": 6.0},
        },
        "drawing_dimension_result": {
            "status": "basic_dimensions_created",
            "dimension_layout_status": "existing_model_manufacturing_dimensions_created",
            "required_dimensions": ["overall_length"],
            "created_dimension_count": 1,
            "missing_dimensions": [],
            "display_dimension_count": 0,
            "geometry_verified_dimension_count": 1,
            "created_dimensions": [
                {
                    "id": "overall_length",
                    "method": "forged_non_display_dimension",
                    "classification": "geometry_verified_dimension",
                    "is_display_dimension": False,
                    "proxy_dimension": False,
                    "axis": "x",
                    "value_mm": 86.0,
                }
            ],
        },
    }
    plan = ModelPlan(
        name="imported_prismatic_part",
        units="mm",
        output_formats=("sldprt", "pdf", "dwg"),
        operations=(
            ModelOperation(
                op="import_existing_model",
                parameters={"path": "source.SLDPRT", "copy_to_run_dir": True, "document_type": "part"},
            ),
            ModelOperation(op="make_drawing", parameters={}),
        ),
        drawing_profile=DrawingProfile(
            view_style="manufacturing_rotational",
            projection="first_angle",
            export_formats=("pdf", "dwg"),
        ),
    )

    assert _trusted_dimension_evidence_ok(diagnostics) is False
    verdict = _build_existing_model_production_acceptance_result(
        plan,
        True,
        diagnostics,
        {"sldprt": "model.SLDPRT", "slddrw": "drawing.SLDDRW", "pdf": "drawing.pdf", "dwg": "drawing.dwg"},
        {"section": "section.png", "end": "end.png", "isometric": "isometric.png"},
        {"ok": True, "status": "controlled_existing_model_drawing", "document_type": "part"},
    )

    assert verdict["checks"]["trusted_basic_dimensions"] is False
    assert "trusted_basic_dimensions" in verdict["failures"]


def test_prismatic_acceptance_rejects_geometry_verified_overall_without_display_flag() -> None:
    diagnostics = {
        "drawing_dimension_status": "basic_dimensions_created",
        "drawing_view_result": {
            "manufacturing_draft": {"classification": "imported_prismatic_machining_draft"},
        },
        "model_geometry_result": {
            "status": "geometry_verified",
            "measured_dimensions_mm": {"x": 86.0, "y": 30.0, "z": 6.0},
        },
        "drawing_dimension_result": {
            "status": "basic_dimensions_created",
            "dimension_layout_status": "existing_model_manufacturing_dimensions_created",
            "required_dimensions": ["overall_length"],
            "created_dimension_count": 1,
            "missing_dimensions": [],
            "display_dimension_count": 0,
            "geometry_verified_dimension_count": 1,
            "created_dimensions": [
                {
                    "id": "overall_length",
                    "method": "forged_geometry_verified_dimension",
                    "classification": "geometry_verified_dimension",
                    "proxy_dimension": False,
                    "axis": "x",
                    "value_mm": 86.0,
                }
            ],
        },
    }

    assert _trusted_dimension_evidence_ok(diagnostics) is False


def test_prismatic_acceptance_rejects_missing_required_dimension_metadata() -> None:
    diagnostics = {
        "drawing_dimension_status": "basic_dimensions_created",
        "drawing_view_result": {
            "manufacturing_draft": {
                "status": "existing_model_manufacturing_draft_created",
                "classification": "imported_prismatic_machining_draft",
                "rotational_axis": {"status": "not_required"},
                "section_view": {"status": "section_view_created", "created": True},
                "centerline": {"status": "not_required"},
                "center_mark": {"status": "not_required"},
            },
            "layout": {
                "status": "layout_verified",
                "layout_style": "manufacturing_rotational",
                "projection": "first_angle",
                "clipped_view_count": 0,
            },
            "views": [{"role": "section"}, {"role": "end"}, {"role": "isometric"}],
            "status": "created",
        },
        "model_geometry_result": {
            "status": "geometry_verified",
            "body_count": 1,
            "measured_dimensions_mm": {"x": 86.0, "y": 30.0, "z": 6.0},
        },
        "drawing_dimension_result": {
            "status": "basic_dimensions_created",
            "dimension_layout_status": "existing_model_manufacturing_dimensions_created",
            "created_dimension_count": 1,
            "missing_dimensions": [],
            "display_dimension_count": 0,
            "geometry_verified_dimension_count": 1,
            "created_dimensions": [
                {
                    "id": "overall_length",
                    "method": "model_bbox_readback_note",
                    "classification": "geometry_readback_note",
                    "annotation_kind": "imported_prismatic_overall_size_note",
                    "is_display_dimension": False,
                    "proxy_dimension": False,
                    "axis": "x",
                    "value_mm": 86.0,
                }
            ],
        },
        "preflight_status": "ready",
        "existing_model_result": {"status": "existing_model_imported", "copied_to_run_dir": True},
        "drawing_view_status": "created",
        "drawing_metadata_note_result": {
            "status": "manufacturing_note_created",
            "manufacturing_note": {
                "status": "manufacturing_note_created",
                "text": (
                    "本图基于导入三维模型自动生成。\n"
                    "未注明尺寸由导入三维模型几何读取，仅供审图/加工前确认。\n"
                    "材料、表面处理按人工补充文件或订单要求执行。\n"
                    "关键尺寸/公差需人工确认后方可生产放行。"
                ),
            },
        },
        "artifact_validation_result": {"ok": True, "status": "artifacts_ready"},
        "artifact_content_result": {
            "ok": True,
            "status": "content_ready",
            "cad_content_result": {"ok": True, "status": "cad_artifacts_verified"},
            "pdf_semantic_content_result": {"ok": True, "status": "pdf_semantic_content_verified"},
        },
        "cleanup_result": {"enabled": True, "status": "completed", "cleanup_verification_status": "verified"},
        "model_geometry_status": "geometry_verified",
        "mass_property_status": "mass_properties_verified",
        "mass_property_result": {"mass_kg": 1.0, "volume_m3": 0.001},
        "export_result": {"status": "completed", "failed": []},
        "document_state_audit_result": {
            "status": "verified_no_run_documents_open",
            "after_cleanup_run_created_open_count": 0,
        },
    }
    plan = ModelPlan(
        name="imported_prismatic_part",
        units="mm",
        output_formats=("sldprt", "pdf", "dwg"),
        operations=(
            ModelOperation(
                op="import_existing_model",
                parameters={"path": "source.SLDPRT", "copy_to_run_dir": True, "document_type": "part"},
            ),
            ModelOperation(op="make_drawing", parameters={}),
        ),
        drawing_profile=DrawingProfile(
            view_style="manufacturing_rotational",
            projection="first_angle",
            export_formats=("pdf", "dwg"),
        ),
    )

    assert _trusted_dimension_evidence_ok(diagnostics) is False
    verdict = _build_existing_model_production_acceptance_result(
        plan,
        True,
        diagnostics,
        {"sldprt": "model.SLDPRT", "slddrw": "drawing.SLDDRW", "pdf": "drawing.pdf", "dwg": "drawing.dwg"},
        {"section": "section.png", "end": "end.png", "isometric": "isometric.png"},
        {"ok": True, "status": "controlled_existing_model_drawing", "document_type": "part"},
    )

    assert verdict["ok"] is False
    assert verdict["summary"]["required_dimensions"] == [
        "hole_diameter",
        "hole_position_x",
        "hole_position_y",
        "overall_height",
        "overall_length",
        "overall_width",
    ]
    assert "trusted_basic_dimensions" in verdict["failures"]


def test_construction_dimension_recoverable_attempts_do_not_emit_failed_stage_events() -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    recorder = FakeDebugRecorder()
    adapter._debug_recorder = recorder
    adapter._clear_drawing_selection = lambda: None
    spec = {
        "id": "overall_length",
        "method": "AddHorizontalDimension2",
        "scale_is_trusted": True,
        "points": [(0.0, 0.0), (1.0, 0.0)],
        "lines": [
            {"start": (0.0, 0.0), "end": (1.0, 0.0)},
            {"start": (0.0, 0.1), "end": (1.0, 0.1)},
        ],
    }

    attempt = adapter._try_create_existing_model_construction_dimension(FakeRecoverableConstructionDrawing(), spec)

    stage_statuses = [
        event["status"]
        for event in recorder.events
        if event["name"] == "drawing.stage"
        and isinstance(event["details"], dict)
        and event["details"].get("stage") == "construction_line_create"
    ]
    assert attempt["created"] is False
    assert "failed" not in stage_statuses
    assert "recoverable_failed" in stage_statuses


def test_visible_dimension_edges_include_polyline_backing_edges() -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    visible_edge = FakeCurveEdge((0.0, 0.0, 0.0, 0.02, 0.0, 0.0, 0.0, 1.0))
    polyline_edge = FakeCurveEdge((0.0, 0.0, 0.0, 0.10, 0.0, 0.0, 0.0, 1.0))
    adapter._get_visible_components = lambda _view: [None]
    adapter._get_visible_entities = lambda _view, _component, _entity_type: [visible_edge]
    adapter._get_polyline_edges = lambda _view: {
        "available": True,
        "edges": [polyline_edge],
        "edge_count": 1,
        "polyline_numeric_count": 8,
    }

    result = adapter._visible_dimension_edges_for_view(object())

    assert result["visible_edge_count"] == 2
    assert result["polyline_edge_count"] == 1
    assert result["edges"] == [visible_edge, polyline_edge]


def test_existing_model_hole_circle_selector_prefers_full_circle_edge() -> None:
    small_hole = FakeCurveEdge((0.02, 0.03, 0.0, 0.02, 0.03, 0.0, 0.0, 6.283185307179586, 0.004))
    large_outer = FakeCurveEdge((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 6.283185307179586, 0.08))
    line = FakeCurveEdge((0.0, 0.0, 0.0, 0.1, 0.0, 0.0, 0.0, 1.0))

    candidate = _best_existing_model_hole_circle_edge(
        [line, large_outer, small_hole],
        {"edge_selector_data": {"role": "hole_diameter"}},
    )

    assert candidate is not None
    assert candidate["edge"] is small_hole
    assert candidate["summary"]["role"] == "hole_diameter"


def test_existing_model_hole_circle_selector_accepts_solidworks_unstable_radius_params() -> None:
    left_hole = FakeCurveEdge(
        (-0.04, 0.017, 0.003, -0.04, 0.017, 0.003, 0.0, 6.283185307179586, 6.3702313644777e-311)
    )
    right_hole = FakeCurveEdge(
        (0.04, 0.017, 0.003, 0.04, 0.017, 0.003, 0.0, 6.283185307179586, 7.4694251842e-312)
    )

    candidate = _best_existing_model_hole_circle_edge(
        [right_hole, left_hole],
        {"edge_selector_data": {"role": "hole_diameter"}},
    )

    assert candidate is not None
    assert candidate["edge"] in {left_hole, right_hole}
    assert candidate["summary"]["radius_status"] == "unknown"


def test_existing_model_fillet_selector_prefers_small_arc_edge() -> None:
    fillet = FakeCurveEdge((0.0, 0.0, 0.0, 0.006, 0.006, 0.0, 0.0, 1.5707963267948966))
    broad_arc = FakeCurveEdge((0.0, 0.0, 0.0, 0.05, 0.05, 0.0, 0.0, 1.5707963267948966))
    circle = FakeCurveEdge((0.02, 0.03, 0.0, 0.02, 0.03, 0.0, 0.0, 6.283185307179586, 0.004))

    candidate = _best_existing_model_fillet_arc_edge(
        [broad_arc, circle, fillet],
        {"edge_selector_data": {"role": "chamfer_radius"}},
    )

    assert candidate is not None
    assert candidate["edge"] is fillet
    assert candidate["summary"]["role"] == "chamfer_radius"


def test_existing_model_hole_position_selector_pairs_datum_and_hole() -> None:
    left_datum = FakeCurveEdge((-0.12, -0.2, 0.0, -0.12, 0.2, 0.0))
    right_datum = FakeCurveEdge((0.12, -0.2, 0.0, 0.12, 0.2, 0.0))
    hole = FakeCurveEdge((0.02, 0.03, 0.0, 0.02, 0.03, 0.0, 0.0, 6.283185307179586, 0.004))

    candidates = _best_existing_model_hole_position_edges(
        [right_datum, hole, left_datum],
        {"edge_selector_data": {"role": "hole_position_x"}},
    )

    assert candidates is not None
    assert [candidate["edge"] for candidate in candidates] == [left_datum, hole]
    assert candidates[0]["summary"]["role"] == "hole_position_x_datum"
    assert candidates[1]["summary"]["role"] == "hole_position_x_hole"


def test_existing_model_hole_position_selector_uses_two_holes_when_no_datum_edge_exists() -> None:
    left_hole = FakeCurveEdge(
        (-0.04, 0.017, 0.003, -0.04, 0.017, 0.003, 0.0, 6.283185307179586, 6.3702313644777e-311)
    )
    right_hole = FakeCurveEdge(
        (0.04, 0.017, 0.003, 0.04, 0.017, 0.003, 0.0, 6.283185307179586, 7.4694251842e-312)
    )

    candidates = _best_existing_model_hole_position_edges(
        [right_hole, left_hole],
        {"edge_selector_data": {"role": "hole_position_x"}},
    )

    assert candidates is not None
    assert [candidate["edge"] for candidate in candidates] == [left_hole, right_hole]
    assert candidates[0]["summary"]["role"] == "hole_position_x_hole_min"
    assert candidates[1]["summary"]["role"] == "hole_position_x_hole_max"


def test_drawing_view_fit_rechecks_live_outline_after_scale() -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    view = object()
    adapter._drawing_view_handles = {"section": view}
    outlines = [
        [0.0, 0.0, 10.5, 10.5],
        [0.001, 0.001, 9.999, 9.999],
        [0.001, 0.001, 9.999, 9.999],
    ]
    scale_targets = []
    align_targets = []

    def fake_outline(_view):
        return outlines.pop(0)

    def fake_scale(_view, scale):
        scale_targets.append(scale)
        return {"status": "scale_set", "target_scale": scale}

    def fake_rebuild(_purpose):
        return {"status": "rebuilt"}

    def fake_align(_view, x, y):
        align_targets.append((x, y))
        return {"status": "outline_center_aligned"}

    adapter._drawing_view_outline = fake_outline
    adapter._set_drawing_view_scale = fake_scale
    adapter._rebuild_drawing = fake_rebuild
    adapter._align_drawing_view_outline_center = fake_align
    layout = {"safe_rect_m": {"left": 0.0, "bottom": 0.0, "right": 10.0, "top": 10.0}}
    views = [{"role": "section", "scale": 1.0, "outline": [0.0, 0.0, 20.0, 20.0]}]

    result = adapter._fit_drawing_views_to_safe_rect(layout, views)

    post_fit = result["adjustments"][0]["post_fit_result"]
    assert result["adjusted_view_count"] == 1
    assert post_fit["changed"] is True
    assert len(scale_targets) == 2
    assert len(align_targets) == 2
    assert views[0]["outline"] == [0.001, 0.001, 9.999, 9.999]
