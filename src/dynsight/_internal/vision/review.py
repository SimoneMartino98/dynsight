"""Reviewed-frame detection comparison for the label tool."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from PIL import Image


def _xyxy(box: dict[str, Any]) -> tuple[float, float, float, float]:
    return (box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"])


def _iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1, ax2, ay2 = _xyxy(a)
    bx1, by1, bx2, by2 = _xyxy(b)
    area = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1)
    )
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - area
    return area / union if union > 0 else 0.0


def match_boxes(
    truth: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    match_iou: float = 0.5,
) -> dict[str, Any]:
    """Greedily match predictions to same-class truth."""
    if not 0 < match_iou <= 1:
        msg = "match_iou must be in (0, 1]."
        raise ValueError(msg)
    used: set[int] = set()
    matches = []
    false_positives = []
    ordered = sorted(
        enumerate(predictions),
        key=lambda item: -float(item[1].get("confidence", 1.0)),
    )
    for pred_idx, pred in ordered:
        candidates = [
            (idx, _iou(gt, pred))
            for idx, gt in enumerate(truth)
            if idx not in used and gt["label"] == pred["label"]
        ]
        best = max(candidates, key=lambda item: item[1], default=None)
        if best is not None and best[1] >= match_iou:
            used.add(best[0])
            matches.append(
                {
                    "truth_index": best[0],
                    "prediction_index": pred_idx,
                    "iou": best[1],
                }
            )
        else:
            false_positives.append(pred_idx)
    missed = [idx for idx in range(len(truth)) if idx not in used]
    return {
        "tp": len(matches),
        "fp": len(false_positives),
        "fn": len(missed),
        "matches": matches,
        "false_positives": false_positives,
        "missed": missed,
        "count_error": len(predictions) - len(truth),
    }


def scores(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0,
    }


def benchmark_snapshot(
    images_dir: Path, session: dict[str, Any]
) -> dict[str, Any]:
    """Snapshot only explicitly reviewed benchmark frames and image hashes."""
    frames = []
    for name, meta in sorted(session.get("frames", {}).items()):
        if not (meta.get("reviewed") and meta.get("split") == "test"):
            continue
        if Path(name).name != name:
            msg = f"Invalid benchmark image name: {name}"
            raise ValueError(msg)
        image = images_dir / name
        if not image.is_file():
            msg = f"Benchmark image missing: {name}"
            raise ValueError(msg)
        with Image.open(image) as im:
            width, height = im.size
        frames.append(
            {
                "name": name,
                "source": meta.get("source", name),
                "frame_index": meta.get("frame_index"),
                "timestamp_ms": meta.get("timestamp_ms"),
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "boxes": deepcopy(
                    session.get("annotations", {}).get(name, [])
                ),
            }
        )
    if not frames:
        msg = "Mark a reviewed frame as test before freezing a benchmark."
        raise ValueError(msg)
    payload = {
        "schema_version": 1,
        "labels": session.get("labels", []),
        "frames": frames,
    }
    encoded = json.dumps(payload, sort_keys=True).encode()
    payload["benchmark_id"] = hashlib.sha256(encoded).hexdigest()[:16]
    return payload


def evaluate_predictions(
    benchmark: dict[str, Any],
    predictions: dict[str, list[dict[str, Any]]],
    match_iou: float = 0.5,
) -> dict[str, Any]:
    """Evaluate boxes against an immutable benchmark snapshot."""
    rows = []
    for frame in benchmark["frames"]:
        name = frame["name"]
        boxes = predictions.get(name, [])
        result = match_boxes(frame["boxes"], boxes, match_iou)
        rows.append(
            {
                "name": name,
                "source": frame["source"],
                "frame_index": frame["frame_index"],
                "width": frame["width"],
                "height": frame["height"],
                "truth_boxes": frame["boxes"],
                "prediction_boxes": boxes,
                **result,
                **scores(result["tp"], result["fp"], result["fn"]),
            }
        )
    tp = sum(row["tp"] for row in rows)
    fp = sum(row["fp"] for row in rows)
    fn = sum(row["fn"] for row in rows)
    curve = []
    for threshold in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95):
        counts = [
            match_boxes(
                frame["boxes"],
                [
                    box
                    for box in predictions.get(frame["name"], [])
                    if float(box.get("confidence", 1.0)) >= threshold
                ],
                match_iou,
            )
            for frame in benchmark["frames"]
        ]
        total_tp = sum(item["tp"] for item in counts)
        total_fp = sum(item["fp"] for item in counts)
        total_fn = sum(item["fn"] for item in counts)
        curve.append(
            {
                "confidence": threshold,
                "tp": total_tp,
                "fp": total_fp,
                "fn": total_fn,
                **scores(total_tp, total_fp, total_fn),
            }
        )
    return {
        "benchmark_id": benchmark["benchmark_id"],
        "match_iou": match_iou,
        "frames": len(rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        **scores(tp, fp, fn),
        "threshold_curve": curve,
        "per_frame": rows,
    }
