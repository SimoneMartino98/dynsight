"""Verified region export and prioritized review for the label tool."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

if TYPE_CHECKING:
    from dynsight._internal.vision.label_tool import _Workspace


def _intersect(
    box: dict[str, Any], region: dict[str, Any]
) -> dict[str, Any] | None:
    x1 = max(float(box["x"]), float(region["x"]))
    y1 = max(float(box["y"]), float(region["y"]))
    x2 = min(float(box["x"] + box["w"]), float(region["x"] + region["w"]))
    y2 = min(float(box["y"] + box["h"]), float(region["y"] + region["h"]))
    if x2 <= x1 or y2 <= y1:
        return None
    return {
        "label": box["label"],
        "x": x1 - region["x"],
        "y": y1 - region["y"],
        "w": x2 - x1,
        "h": y2 - y1,
        "border_truncated": (
            x1 > box["x"]
            or y1 > box["y"]
            or x2 < box["x"] + box["w"]
            or y2 < box["y"] + box["h"]
        ),
    }


def verified_region_records(
    workspace: _Workspace, session: dict[str, Any]
) -> list[dict[str, Any]]:
    """Validate trusted regions and their current frame provenance."""
    records = []
    test_sources = {
        meta.get("source", name)
        for name, meta in session.get("frames", {}).items()
        if meta.get("split") == "test"
    }
    for name, regions in session.get("regions", {}).items():
        if Path(name).name != name:
            msg = f"Invalid region frame name: {name}"
            raise ValueError(msg)
        image = workspace.images_dir / name
        if not image.is_file():
            msg = f"Region frame is missing: {name}"
            raise ValueError(msg)
        meta = session.get("frames", {}).get(name, {})
        source = meta.get("source", name)
        if meta.get("split") == "test" or source in test_sources:
            continue
        with Image.open(image) as img:
            width, height = img.size
        for region in regions:
            if not region.get("reviewed"):
                continue
            x, y, w, h = (int(region[key]) for key in ("x", "y", "w", "h"))
            if (
                x < 0
                or y < 0
                or w <= 0
                or h <= 0
                or x + w > width
                or y + h > height
            ):
                msg = f"Invalid region bounds: {region.get('id')}"
                raise ValueError(msg)
            boxes = [
                clipped
                for box in session.get("annotations", {}).get(name, [])
                if (clipped := _intersect(box, region)) is not None
            ]
            records.append(
                {
                    "name": name,
                    "source": source,
                    "frame_index": meta.get("frame_index"),
                    "timestamp_ms": meta.get("timestamp_ms"),
                    "region": region,
                    "boxes": boxes,
                }
            )
    return records


def export_verified_regions(
    workspace: _Workspace,
    session: dict[str, Any],
    name: str,
    train_split: float = 0.8,
    seed: int | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Export only fully verified regions as a YOLO dataset."""
    from dynsight._internal.vision.label_tool import (
        _dataset_dirs,
        _safe_name,
        _split_count,
        _write_dataset_yaml,
        _yolo_lines,
    )

    if not 0 < train_split < 1:
        msg = "train_split must be between 0 and 1."
        raise ValueError(msg)
    records = verified_region_records(workspace, session)
    if not records:
        msg = "No verified non-test regions to export."
        raise ValueError(msg)
    labels = [item["name"] for item in session.get("labels", [])]
    if not labels:
        msg = "No labels defined."
        raise ValueError(msg)
    class_ids = {value: idx for idx, value in enumerate(labels)}
    for record in records:
        for box in record["boxes"]:
            if box["label"] not in class_ids:
                msg = f"Unknown label in region: {box['label']}"
                raise ValueError(msg)
    base = output_dir if output_dir is not None else workspace.root
    dataset = (base / _safe_name(name)).resolve()
    if dataset.exists():
        msg = f"Dataset already exists: {dataset}"
        raise ValueError(msg)
    dirs = _dataset_dirs(dataset)
    sources = sorted({item["source"] for item in records})
    random.Random(seed).shuffle(sources)  # noqa: S311
    if len(sources) > 1:
        train_sources = set(sources[: _split_count(len(sources), train_split)])
        split_policy = "grouped_by_source"
    else:
        train_sources = set()
        split_policy = "single_source_regions_correlated"
    manifest = []
    for idx, item in enumerate(records):
        split = (
            "train"
            if item["source"] in train_sources
            else "val"
            if len(sources) > 1
            else "train"
            if idx < _split_count(len(records), train_split)
            else "val"
        )
        region = item["region"]
        filename = f"region_{idx:05d}.png"
        with Image.open(workspace.images_dir / item["name"]) as image:
            crop = image.crop(
                (
                    region["x"],
                    region["y"],
                    region["x"] + region["w"],
                    region["y"] + region["h"],
                )
            )
            crop.save(dirs[f"images/{split}"] / filename)
        lines = _yolo_lines(item["boxes"], class_ids, region["w"], region["h"])
        (dirs[f"labels/{split}"] / f"region_{idx:05d}.txt").write_text(
            lines, encoding="utf-8"
        )
        manifest.append(
            {
                "image": filename,
                "source_frame": item["name"],
                "source": item["source"],
                "frame_index": item["frame_index"],
                "timestamp_ms": item["timestamp_ms"],
                "region": region,
                "split": split,
                "box_count": len(item["boxes"]),
                "border_policy": "clip_all_intersecting_boxes",
            }
        )
    yaml_path = _write_dataset_yaml(dataset, labels)
    (dataset / "region_manifest.json").write_text(
        json.dumps(
            {
                "split_policy": split_policy,
                "regions": manifest,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "path": str(dataset),
        "yaml": str(yaml_path),
        "regions": len(records),
        "num_train": sum(row["split"] == "train" for row in manifest),
        "num_val": sum(row["split"] == "val" for row in manifest),
        "split_policy": split_policy,
    }


def export_pseudo_dataset(  # noqa: C901, PLR0915
    workspace: _Workspace,
    session: dict[str, Any],
    dataset_name: str,
    confidence: float = 0.9,
    match_iou: float = 0.5,
    max_frame_gap: int = 5,
    train_split: float = 0.8,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Export high-confidence, adjacent-frame-consistent drafts separately."""
    import shutil

    from dynsight._internal.vision.label_tool import (
        _dataset_dirs,
        _safe_name,
        _split_count,
        _write_dataset_yaml,
        _yolo_lines,
    )
    from dynsight._internal.vision.review import match_boxes

    if not 0 < confidence <= 1 or not 0 < match_iou <= 1:
        msg = "Confidence and matching IoU must be in (0, 1]."
        raise ValueError(msg)
    if max_frame_gap < 1 or not 0 < train_split < 1:
        msg = "Invalid frame gap or train split."
        raise ValueError(msg)
    frames = session.get("frames", {})
    annotations = session.get("annotations", {})
    test_sources = {
        meta.get("source", name)
        for name, meta in frames.items()
        if meta.get("split") == "test"
    }
    labels = [item["name"] for item in session.get("labels", [])]
    class_ids = {value: idx for idx, value in enumerate(labels)}
    records = []
    for name, meta in frames.items():
        source = meta.get("source", name)
        index = meta.get("frame_index")
        if (
            meta.get("reviewed")
            or meta.get("split") == "test"
            or source in test_sources
            or not isinstance(index, int)
            or not (workspace.images_dir / name).is_file()
        ):
            continue
        neighbors = [
            other
            for other, other_meta in frames.items()
            if other != name
            and other_meta.get("source", other) == source
            and isinstance(other_meta.get("frame_index"), int)
            and 0 < abs(other_meta["frame_index"] - index) <= max_frame_gap
        ]
        consistent = []
        for box in annotations.get(name, []):
            if (
                box.get("provenance")
                not in {"model_draft", "propagated_draft"}
                or float(box.get("confidence", 0)) < confidence
                or box.get("label") not in class_ids
            ):
                continue
            if any(
                match_boxes(
                    [box],
                    [
                        neighbor
                        for neighbor in annotations.get(other, [])
                        if neighbor.get("label") == box["label"]
                    ],
                    match_iou,
                )["tp"]
                == 1
                for other in neighbors
            ):
                consistent.append(box)
        if consistent:
            records.append(
                {
                    "name": name,
                    "source": source,
                    "frame_index": index,
                    "boxes": consistent,
                    "neighbors": neighbors,
                }
            )
    if not records:
        msg = "No cross-frame-consistent draft boxes meet this policy."
        raise ValueError(msg)
    base = output_dir if output_dir is not None else workspace.root
    dataset = (base / _safe_name(dataset_name)).resolve()
    if dataset.exists():
        msg = f"Dataset already exists: {dataset}"
        raise ValueError(msg)
    dirs = _dataset_dirs(dataset)
    sources = sorted({row["source"] for row in records})
    train_sources = set(sources[: _split_count(len(sources), train_split)])
    train_count = _split_count(len(records), train_split)
    manifest = []
    for position, row in enumerate(records):
        split = (
            ("train" if row["source"] in train_sources else "val")
            if len(sources) > 1
            else ("train" if position < train_count else "val")
        )
        image_path = workspace.images_dir / row["name"]
        with Image.open(image_path) as image:
            width, height = image.size
        shutil.copy2(image_path, dirs[f"images/{split}"] / row["name"])
        txt = _yolo_lines(row["boxes"], class_ids, width, height)
        (dirs[f"labels/{split}"] / f"{Path(row['name']).stem}.txt").write_text(
            txt, encoding="utf-8"
        )
        manifest.append(
            {
                "name": row["name"],
                "source": row["source"],
                "frame_index": row["frame_index"],
                "split": split,
                "admitted_boxes": len(row["boxes"]),
                "draft_boxes": len(annotations.get(row["name"], [])),
                "supporting_frames": row["neighbors"],
            }
        )
    yaml_path = _write_dataset_yaml(dataset, labels)
    (dataset / "pseudo_manifest.json").write_text(
        json.dumps(
            {
                "status": "model_generated_unreviewed",
                "policy": {
                    "confidence": confidence,
                    "match_iou": match_iou,
                    "max_frame_gap": max_frame_gap,
                },
                "split_policy": (
                    "grouped_by_source"
                    if len(sources) > 1
                    else "single_source_correlated"
                ),
                "warning": (
                    "Unlabeled objects may remain in full-frame images."
                ),
                "frames": manifest,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "path": str(dataset),
        "yaml": str(yaml_path),
        "frames": len(records),
        "boxes": sum(len(r["boxes"]) for r in records),
    }
