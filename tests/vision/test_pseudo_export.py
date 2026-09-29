"""Pseudo-label export keeps model proposals separate from trusted data."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

from PIL import Image

from dynsight._internal.vision.label_tool import _Workspace
from dynsight._internal.vision.region_review import export_pseudo_dataset

if TYPE_CHECKING:
    from pathlib import Path


def test_pseudo_export_needs_confidence_and_adjacent_support(
    tmp_path: Path,
) -> None:
    ws = _Workspace(tmp_path / "ws")
    output = io.BytesIO()
    Image.new("RGB", (64, 64)).save(output, format="PNG")
    for name in ("a.png", "b.png", "test.png"):
        ws.add_image(name, output.getvalue())
    box = {
        "label": "cell",
        "x": 10,
        "y": 10,
        "w": 10,
        "h": 10,
        "confidence": 0.95,
        "provenance": "model_draft",
    }
    session = {
        "labels": [{"name": "cell"}],
        "frames": {
            "a.png": {"source": "video", "frame_index": 0, "split": "train"},
            "b.png": {"source": "video", "frame_index": 1, "split": "train"},
            "test.png": {
                "source": "holdout",
                "frame_index": 0,
                "split": "test",
            },
        },
        "annotations": {
            "a.png": [box, {**box, "x": 40, "confidence": 0.2}],
            "b.png": [dict(box)],
            "test.png": [dict(box)],
        },
    }
    result = export_pseudo_dataset(
        ws,
        session,
        "pseudo",
        confidence=0.9,
        max_frame_gap=1,
    )
    manifest = json.loads(
        (tmp_path / "ws" / "pseudo" / "pseudo_manifest.json").read_text()
    )
    assert result["boxes"] == 2  # noqa: PLR2004
    assert manifest["status"] == "model_generated_unreviewed"
    assert all(row["source"] == "video" for row in manifest["frames"])
    assert all(row["admitted_boxes"] == 1 for row in manifest["frames"])
