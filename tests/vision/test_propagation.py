"""Optical-flow proposals stay drafts and preserve unmatched predictions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from dynsight._internal.vision.propagation import propose_next_frame

if TYPE_CHECKING:
    from pathlib import Path


def test_propagation_is_draft_and_keeps_existing(tmp_path: Path) -> None:
    for name, offset in (("source.png", 0), ("target.png", 3)):
        image = Image.new("RGB", (96, 96), "black")
        draw = ImageDraw.Draw(image)
        draw.ellipse((30 + offset, 30, 42 + offset, 42), fill="white")
        image.save(tmp_path / name)
    corrected = [{"label": "cell", "x": 29, "y": 29, "w": 15, "h": 15}]
    existing = [{"label": "other", "x": 70, "y": 70, "w": 10, "h": 10}]
    result = propose_next_frame(
        tmp_path / "source.png",
        tmp_path / "target.png",
        corrected,
        existing,
        "source.png",
    )
    assert len(result) == 2  # noqa: PLR2004
    assert result[0]["provenance"] == "propagated_draft"
    assert result[0]["source_frame"] == "source.png"
    assert all(result[1][key] == value for key, value in existing[0].items())
    assert result[1]["uncertain"]
    assert "new detection or track start" in result[1]["uncertainty_reasons"]
    assert corrected == [
        {"label": "cell", "x": 29, "y": 29, "w": 15, "h": 15}
    ]  # source is never modified
