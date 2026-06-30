from __future__ import annotations

import json

from solidworks_mcp.adapters.solidworks import _drawing_view_result_payload


class FakeComHandle:
    pass


def test_drawing_view_result_payload_drops_com_view_handle() -> None:
    flat_pattern_result = {
        "status": "created",
        "view": FakeComHandle(),
        "summary": {"role": "flat_pattern"},
        "errors": [],
    }

    payload = _drawing_view_result_payload(flat_pattern_result)

    assert "view" not in payload
    assert payload["summary"] == {"role": "flat_pattern"}
    assert payload["status"] == "created"
    json.dumps(payload)
