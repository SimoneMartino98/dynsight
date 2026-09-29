"""Conservative optical-flow drafts between nearby video frames."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

_MAX_FLOW_ERROR = 40


def propose_next_frame(  # noqa: C901, PLR0912, PLR0915
    source_path: Path,
    target_path: Path,
    corrected: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    source_name: str,
) -> list[dict[str, Any]]:
    """Track corrected box centers and keep all outputs unreviewed drafts."""
    import cv2
    import numpy as np

    source = cv2.imread(str(source_path), cv2.IMREAD_GRAYSCALE)
    target = cv2.imread(str(target_path), cv2.IMREAD_GRAYSCALE)
    if source is None or target is None:
        msg = "Could not read the source or target video frame."
        raise ValueError(msg)
    if source.shape != target.shape:
        msg = "Adjacent frames must have the same image dimensions."
        raise ValueError(msg)
    if not corrected:
        return [dict(box) for box in existing]
    points = np.array(
        [
            [[box["x"] + box["w"] / 2, box["y"] + box["h"] / 2]]
            for box in corrected
        ],
        dtype=np.float32,
    )
    moved, status, errors = cv2.calcOpticalFlowPyrLK(
        source,
        target,
        points,
        None,
    )
    used = set()
    proposals = []
    height, width = target.shape
    for idx, box in enumerate(corrected):
        good = (
            moved is not None
            and status is not None
            and bool(status[idx][0])
            and errors is not None
            and errors[idx][0] < _MAX_FLOW_ERROR
        )
        dx = float(moved[idx][0][0] - points[idx][0][0]) if good else 0.0
        dy = float(moved[idx][0][1] - points[idx][0][1]) if good else 0.0
        reasons = []
        if not good:
            reasons.append("tracking lost")
        if abs(dx) > box["w"] * 2 or abs(dy) > box["h"] * 2:
            reasons.append("sudden motion")
        shifted = dict(box)
        shifted["x"] = max(0.0, min(width - box["w"], box["x"] + dx))
        shifted["y"] = max(0.0, min(height - box["h"], box["y"] + dy))
        candidates = [
            (n, draft)
            for n, draft in enumerate(existing)
            if n not in used and draft.get("label") == box.get("label")
        ]
        nearest = min(
            candidates,
            key=lambda item: (item[1]["x"] - shifted["x"]) ** 2
            + (item[1]["y"] - shifted["y"]) ** 2,
            default=None,
        )
        if nearest is not None and (
            (nearest[1]["x"] - shifted["x"]) ** 2
            + (nearest[1]["y"] - shifted["y"]) ** 2
        ) ** 0.5 < max(box["w"], box["h"]):
            used.add(nearest[0])
            proposal = dict(nearest[1])
        else:
            proposal = shifted
            reasons.append("no matching prediction")
        if (
            proposal["x"] <= 0
            or proposal["y"] <= 0
            or proposal["x"] + proposal["w"] >= width
            or proposal["y"] + proposal["h"] >= height
        ):
            reasons.append("border entry or exit")
        proposal["provenance"] = "propagated_draft"
        proposal["source_frame"] = source_name
        proposal["uncertainty_reasons"] = reasons
        proposal["uncertain"] = bool(proposal.get("uncertain") or reasons)
        proposals.append(proposal)
    proposals.extend(
        {
            **box,
            "uncertain": True,
            "uncertainty_reasons": ["new detection or track start"],
        }
        for idx, box in enumerate(existing)
        if idx not in used
    )
    for idx, box in enumerate(proposals):
        for other in proposals[idx + 1 :]:
            if box.get("label") != other.get("label"):
                continue
            x_overlap = max(
                0,
                min(box["x"] + box["w"], other["x"] + other["w"])
                - max(box["x"], other["x"]),
            )
            y_overlap = max(
                0,
                min(box["y"] + box["h"], other["y"] + other["h"])
                - max(box["y"], other["y"]),
            )
            if (
                x_overlap * y_overlap
                > min(box["w"] * box["h"], other["w"] * other["h"]) / 2
            ):
                box["uncertain"] = True
                box.setdefault("uncertainty_reasons", []).append("overlap")
                other["uncertain"] = True
                other.setdefault("uncertainty_reasons", []).append("overlap")
    return proposals
