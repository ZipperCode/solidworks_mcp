from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Final, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonMap: TypeAlias = dict[str, JsonValue]

DEFAULT_SOURCE_DIR: Final = Path(r"C:\Users\Zipper\Downloads\解密3D")
SUPPORTED_SUFFIXES: Final = (".sldprt", ".sldasm")


@dataclass(frozen=True, slots=True)
class PartCase:
    index: int
    path: Path
    slug: str
    document_type: str


def part_cases(source_dir: Path, *, start: int = 1, max_parts: int | None = None) -> list[PartCase]:
    files = [
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    sorted_files = sorted(files, key=lambda path: path.stat().st_size, reverse=True)
    selected = sorted_files[start - 1 :]
    if max_parts is not None:
        selected = selected[:max_parts]
    return [
        PartCase(
            index=start + offset,
            path=path,
            slug=slug_for_part(start + offset, path),
            document_type=document_type_for_path(path),
        )
        for offset, path in enumerate(selected)
    ]


def slug_for_part(index: int, path: Path) -> str:
    ascii_stem = re.sub(r"[^0-9A-Za-z]+", "_", path.stem).strip("_").lower()
    digest = hashlib.sha1(path.name.encode("utf-8")).hexdigest()[:8]
    stem = ascii_stem[:32] if ascii_stem else "part"
    return f"{index:03d}_{stem}_{digest}"


def document_type_for_path(path: Path) -> str:
    return "assembly" if path.suffix.lower() == ".sldasm" else "part"


def model_plan_for_case(part_case: PartCase) -> JsonMap:
    is_assembly = part_case.document_type == "assembly"
    native_format = "sldasm" if is_assembly else "sldprt"
    view_style = "assembly_general" if is_assembly else "manufacturing_rotational"
    plan_name = f"decrypted_{part_case.slug}"
    return {
        "name": plan_name,
        "units": "mm",
        "metadata": {
            "batch_source": "decrypted_3d_real_parts",
            "source_model": str(part_case.path),
            "part_index": part_case.index,
            "document_type": part_case.document_type,
        },
        "output_formats": [native_format, "step", "slddrw", "pdf", "dwg"],
        "operations": [
            {
                "id": "import_existing_model",
                "op": "import_existing_model",
                "parameters": {
                    "path": str(part_case.path),
                    "copy_to_run_dir": True,
                    "document_type": part_case.document_type,
                    "reference_search_paths": [str(part_case.path.parent)],
                },
            },
            {"id": "make_review_drawing", "op": "make_drawing", "parameters": {}},
        ],
        "drawing_profile": {
            "enabled": True,
            "sheet_format": "A3",
            "projection": "first_angle",
            "view_style": view_style,
            "include_isometric": True,
            "include_basic_dimensions": True,
            "export_formats": ["pdf", "dwg"],
            "auto_layout": True,
            "margin_mm": 18,
            "title_block_height_mm": 42,
        },
    }
