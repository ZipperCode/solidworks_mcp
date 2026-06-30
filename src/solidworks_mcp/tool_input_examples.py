from __future__ import annotations

from pydantic import JsonValue


DRAWING_PROFILE_EXAMPLES: list[dict[str, JsonValue]] = [
    {
        "enabled": True,
        "sheet_format": "A3",
        "projection": "first_angle",
        "view_style": "standard",
        "include_isometric": True,
        "include_basic_dimensions": True,
        "export_formats": ["pdf", "dwg"],
    },
    {
        "enabled": True,
        "template_path": "C:\\ProgramData\\SOLIDWORKS\\SOLIDWORKS 2022\\templates\\gb_a3.drwdot",
        "sheet_format": "A3",
        "projection": "first_angle",
        "view_style": "manufacturing_rotational",
        "export_formats": ["pdf", "dwg", "slddrw"],
    },
]


OPERATION_EXAMPLES: list[dict[str, JsonValue]] = [
    {
        "op": "create_sketch",
        "id": "front_profile",
        "parameters": {
            "plane": "front",
            "entities": [
                {
                    "type": "center_rectangle",
                    "center": [0, 0],
                    "width": 13,
                    "height": 10.2,
                },
            ],
        },
    },
    {
        "op": "create_sketch",
        "id": "front_profile",
        "plane": "front",
        "entities": [
            {
                "type": "center_rectangle",
                "center": [0, 0],
                "width": 13,
                "height": 10.2,
            },
        ],
    },
    {
        "op": "extrude",
        "id": "head_block",
        "sketch_id": "front_profile",
        "depth": 20,
        "direction": "+z",
        "merge": True,
    },
    {
        "op": "hole",
        "id": "through_hole",
        "target_face": "head_block.top",
        "position": [8, 0],
        "diameter": 4,
        "depth": 10.2,
    },
    {
        "op": "chamfer",
        "id": "front_c1",
        "target_refs": ["head_block.front_top_edges"],
        "distance": 1,
    },
]


MODEL_PLAN_EXAMPLES: list[dict[str, JsonValue]] = [
    {
        "name": "stepped_pin_demo",
        "units": "mm",
        "operations": [
            {
                "op": "create_sketch",
                "id": "head_profile",
                "plane": "front",
                "entities": [
                    {
                        "type": "center_rectangle",
                        "center": [0, 0],
                        "width": 13,
                        "height": 10.2,
                    },
                ],
            },
            {
                "op": "extrude",
                "id": "head_block",
                "sketch_id": "head_profile",
                "depth": 20,
                "direction": "+z",
            },
        ],
        "drawing_profile": {
            "enabled": True,
            "sheet_format": "A3",
            "projection": "first_angle",
            "export_formats": ["pdf", "dwg"],
        },
        "output_formats": ["sldprt", "step", "stl"],
    },
]
