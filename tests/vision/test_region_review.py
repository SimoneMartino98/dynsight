"""Region review and visual report invariants."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from dynsight._internal.vision.comparison_report import write_comparison_html
from dynsight._internal.vision.label_tool import _Workspace, synthesize_dataset
from dynsight._internal.vision.region_review import (
    export_verified_regions,
    verified_region_records,
)

if TYPE_CHECKING:
    from pathlib import Path


def _image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(output, format="PNG")
    return output.getvalue()


def test_verified_regions_export_and_test_source_exclusion(
    tmp_path: Path,
) -> None:
    ws = _Workspace(tmp_path / "ws")
    for name in ("train.png", "test.png"):
        ws.add_image(name, _image())
    session = {
        "labels": [{"name": "cell"}],
        "annotations": {
            "train.png": [{"label": "cell", "x": 28, "y": 2, "w": 12, "h": 12}]
        },
        "frames": {
            "train.png": {"source": "train_video", "split": "train"},
            "test.png": {"source": "test_video", "split": "test"},
        },
        "regions": {
            "train.png": [
                {
                    "id": "a",
                    "x": 0,
                    "y": 0,
                    "w": 32,
                    "h": 32,
                    "reviewed": True,
                },
                {
                    "id": "b",
                    "x": 32,
                    "y": 0,
                    "w": 32,
                    "h": 32,
                    "reviewed": True,
                },
                {
                    "id": "c",
                    "x": 0,
                    "y": 32,
                    "w": 32,
                    "h": 32,
                    "reviewed": False,
                },
            ],
            "test.png": [
                {"id": "d", "x": 0, "y": 0, "w": 32, "h": 32, "reviewed": True}
            ],
        },
    }
    records = verified_region_records(ws, session)
    assert len(records) == 2  # noqa: PLR2004
    assert records[0]["boxes"][0]["border_truncated"] is True
    result = export_verified_regions(ws, session, "regions", seed=0)
    manifest = json.loads(
        (tmp_path / "ws" / "regions" / "region_manifest.json").read_text()
    )
    assert result["regions"] == 2  # noqa: PLR2004
    assert all(item["source"] == "train_video" for item in manifest["regions"])
    assert all(
        item["border_policy"] == "clip_all_intersecting_boxes"
        for item in manifest["regions"]
    )
    assert not any(
        item["source_frame"] == "test.png" for item in manifest["regions"]
    )


def test_real_background_requires_verified_empty_region(
    tmp_path: Path,
) -> None:
    ws = _Workspace(tmp_path / "ws")
    ws.add_image("frame.png", _image())
    session = {
        "labels": [{"name": "cell"}],
        "annotations": {
            "frame.png": [{"label": "cell", "x": 0, "y": 0, "w": 10, "h": 10}]
        },
        "frames": {"frame.png": {"reviewed": True, "split": "train"}},
        "regions": {},
    }
    with pytest.raises(ValueError, match="Verify an empty region"):
        synthesize_dataset(
            ws,
            session,
            "synt",
            background_mode="real",
        )
    session["regions"]["frame.png"] = [
        {"id": "empty", "x": 32, "y": 32, "w": 32, "h": 32, "reviewed": True}
    ]
    result = synthesize_dataset(
        ws,
        session,
        "synt",
        num_images=2,
        per_image=1,
        width=64,
        height=64,
        background_mode="real",
        seed=1,
    )
    manifest = json.loads(
        (tmp_path / "ws" / "synt" / "source_manifest.json").read_text()
    )
    assert result["num_train"] == 1
    assert manifest["background_mode"] == "real"
    assert all(
        item["background_frame"] == "frame.png" for item in manifest["images"]
    )


def test_comparison_html_contains_embedded_preview(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    (images / "frame.png").write_bytes(_image())
    row = {
        "name": "frame.png",
        "width": 64,
        "height": 64,
        "truth_boxes": [],
        "prediction_boxes": [],
        "false_positives": [],
        "missed": [],
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "count_error": 0,
    }
    report = {
        "benchmark_id": "example",
        "match_iou": 0.5,
        "warnings": [],
        "reports": [
            {
                "model": "model.pt",
                "per_frame": [row],
                "frames": 1,
                "precision": 0,
                "recall": 0,
                "f1": 0,
                "tp": 0,
                "fp": 0,
                "fn": 0,
                "threshold_curve": [],
            }
        ],
    }
    path = write_comparison_html(report, images, tmp_path / "report.html")
    html = path.read_text()
    assert "data:image/jpeg;base64," in html
    assert "frame.png" in html
    assert "Print / Save PDF" in html
