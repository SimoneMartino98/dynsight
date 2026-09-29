"""Portable visual reports for label-tool model comparisons."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


def _preview(path: Path, max_width: int = 900) -> str:
    """Encode a compact copy of a frame without changing the source image."""
    with Image.open(path) as image:
        frame = image.convert("RGB")
        frame.thumbnail((max_width, max_width))
        output = io.BytesIO()
        frame.save(output, format="JPEG", quality=78)
    return (
        "data:image/jpeg;base64,"
        + base64.b64encode(output.getvalue()).decode()
    )


def write_comparison_html(
    report: dict[str, Any], images_dir: Path, path: Path
) -> Path:
    """Write a standalone interactive report with embedded frame previews."""
    frames = report["reports"][0]["per_frame"] if report["reports"] else []
    payload = {
        **report,
        "previews": {
            row["name"]: _preview(images_dir / row["name"]) for row in frames
        },
    }
    # A literal script close in a filename or label must remain data.
    encoded = json.dumps(payload).replace("</", "<\\/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (Path(__file__).parent / "label_tool" / "comparison.html")
        .read_text(encoding="utf-8")
        .replace("__REPORT_DATA__", encoded),
        encoding="utf-8",
    )
    return path


def write_comparison_assets(
    report: dict[str, Any], images_dir: Path, directory: Path
) -> list[str]:
    """Save representative overlays and editable SVG metric curves."""
    directory.mkdir(parents=True, exist_ok=True)
    assets = []
    if not report["reports"]:
        return assets
    worst = sorted(
        report["reports"][0]["per_frame"],
        key=lambda row: -(row["fp"] + row["fn"]),
    )[:5]
    for frame_index, summary in enumerate(worst):
        for model_index, model in enumerate(report["reports"]):
            row = next(
                item
                for item in model["per_frame"]
                if item["name"] == summary["name"]
            )
            with Image.open(images_dir / row["name"]) as original:
                image = original.convert("RGB")
                image.thumbnail((1200, 1200))
            draw = ImageDraw.Draw(image)
            sx = image.width / row["width"]
            sy = image.height / row["height"]
            for idx, box in enumerate(row["truth_boxes"]):
                color = "#ffbb45" if idx in row["missed"] else "#37d67a"
                draw.rectangle(_box_xyxy(box, sx, sy), outline=color, width=2)
            for idx, box in enumerate(row["prediction_boxes"]):
                color = (
                    "#ff5e70" if idx in row["false_positives"] else "#20b7e8"
                )
                draw.rectangle(_box_xyxy(box, sx, sy), outline=color, width=2)
            name = f"frame_{frame_index:02d}_model_{model_index:02d}.jpg"
            image.save(directory / name, quality=82)
            assets.append(name)
    for metric in ("precision", "recall", "f1"):
        points = report["reports"][0]["threshold_curve"]
        coords = " ".join(
            f"{30 + float(item['confidence']) * 440:.1f},"
            f"{210 - float(item[metric]) * 180:.1f}"
            for item in points
        )
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 500 240">'
            '<rect width="500" height="240" fill="#fff"/>'
            '<path d="M30 20 V210 H480" stroke="#333" fill="none"/>'
            f'<polyline points="{coords}" stroke="#2563eb" '
            'stroke-width="3" fill="none"/>'
            f'<text x="30" y="20">{metric} by confidence</text></svg>'
        )
        name = f"{metric}_curve.svg"
        (directory / name).write_text(svg, encoding="utf-8")
        assets.append(name)
    return assets


def _box_xyxy(
    box: dict[str, Any], sx: float, sy: float
) -> tuple[float, float, float, float]:
    return (
        box["x"] * sx,
        box["y"] * sy,
        (box["x"] + box["w"]) * sx,
        (box["y"] + box["h"]) * sy,
    )
