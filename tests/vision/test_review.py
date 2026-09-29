from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from io import BytesIO
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar

import pytest

from dynsight._internal.vision.label_tool import (
    _LabelToolServer,
    _Workspace,
    export_dataset,
)
from dynsight._internal.vision.review import (
    benchmark_snapshot,
    evaluate_predictions,
    match_boxes,
)

if TYPE_CHECKING:
    from pathlib import Path


def box(x: float, confidence: float = 1.0) -> dict:
    return {
        "label": "cell",
        "x": x,
        "y": 0.0,
        "w": 10.0,
        "h": 10.0,
        "confidence": confidence,
    }


def test_matching_reports_duplicates_and_misses() -> None:
    result = match_boxes([box(0), box(30)], [box(0), box(0, 0.5)])
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 1)
    assert result["false_positives"] == [1]
    assert result["missed"] == [1]


def test_frozen_test_frame_cannot_enter_training_export(
    tmp_path: Path,
) -> None:
    from io import BytesIO

    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (32, 32)).save(image, format="PNG")
    workspace = _Workspace(tmp_path)
    workspace.add_image("test.png", image.getvalue())
    workspace.add_image("train.png", image.getvalue())
    session = {
        "labels": [{"name": "cell", "color": "#ff0000"}],
        "annotations": {"test.png": [box(0)], "train.png": []},
        "frames": {
            "test.png": {"reviewed": True, "split": "test"},
            "train.png": {"reviewed": True, "split": "train"},
        },
    }
    frozen = benchmark_snapshot(workspace.images_dir, session)
    assert len(frozen["frames"]) == 1
    original_id = frozen["benchmark_id"]
    session["annotations"]["test.png"].clear()
    assert frozen["frames"][0]["boxes"] == [box(0)]
    assert frozen["benchmark_id"] == original_id
    result = evaluate_predictions(frozen, {"test.png": [box(0), box(20)]})
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 0)
    export_dataset(workspace, session, "training")
    assert not list((tmp_path / "training").rglob("test.png"))
    assert list((tmp_path / "training").rglob("train.txt"))
    with pytest.raises(ValueError, match="No reviewed"):
        export_dataset(
            workspace,
            {**session, "frames": {"test.png": session["frames"]["test.png"]}},
            "nothing",
        )


def test_source_frames_stay_in_one_split(tmp_path: Path) -> None:
    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (24, 24)).save(image, format="PNG")
    workspace = _Workspace(tmp_path)
    frames = {}
    for source in ("first.mp4", "second.mp4"):
        for index in range(2):
            name = f"{source}_{index}.png"
            workspace.add_image(name, image.getvalue())
            frames[name] = {
                "reviewed": True,
                "split": "train",
                "source": source,
            }
    session = {
        "labels": [{"name": "cell", "color": "#ff0000"}],
        "annotations": {},
        "frames": frames,
    }
    result = export_dataset(workspace, session, "grouped", shuffle=False)
    assert result["split_policy"] == "grouped_by_source"
    assert result["num_train"] == 2  # noqa: PLR2004
    assert result["num_val"] == 2  # noqa: PLR2004


def test_test_source_is_excluded_from_training(tmp_path: Path) -> None:
    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (24, 24)).save(image, format="PNG")
    workspace = _Workspace(tmp_path)
    for name in ("test.png", "same_source.png", "other.png"):
        workspace.add_image(name, image.getvalue())
    session = {
        "labels": [{"name": "cell"}],
        "annotations": {},
        "frames": {
            "test.png": {"reviewed": True, "split": "test", "source": "a"},
            "same_source.png": {
                "reviewed": True,
                "split": "train",
                "source": "a",
            },
            "other.png": {"reviewed": True, "split": "train", "source": "b"},
        },
    }
    result = export_dataset(workspace, session, "safe")
    assert result["num_train"] == 1
    assert not list((tmp_path / "safe").rglob("same_source.png"))


def test_benchmark_and_comparison_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    class Array(list):
        def tolist(self) -> list:
            return list(self)

    settings: list[dict[str, object]] = []

    class FakeYOLO:
        names: ClassVar[dict[int, str]] = {0: "cell"}

        def __init__(self, _path: str) -> None:
            pass

        def predict(self, _path: str, **kwargs: object) -> list:
            settings.append(kwargs)
            return [
                SimpleNamespace(
                    boxes=SimpleNamespace(
                        xyxy=Array([[0, 0, 10, 10]]),
                        cls=Array([0]),
                        conf=Array([0.9]),
                    )
                )
            ]

    import ultralytics

    monkeypatch.setattr(ultralytics, "YOLO", FakeYOLO)
    workspace = _Workspace(tmp_path / "ws")
    image = BytesIO()
    Image.new("RGB", (24, 24)).save(image, format="PNG")
    workspace.add_image("frame.png", image.getvalue())
    (tmp_path / "first.pt").write_bytes(b"fake checkpoint")
    server = _LabelToolServer(0, workspace)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path: str, payload: dict) -> dict:
        request = urllib.request.Request(  # noqa: S310
            base + path,
            data=json.dumps(payload).encode(),
            method="POST",
        )
        with urllib.request.urlopen(request) as response:  # noqa: S310
            return json.loads(response.read())

    try:
        post(
            "/api/sync",
            {
                "labels": [{"name": "cell", "color": "#ff0000"}],
                "annotations": {"frame.png": [box(0)]},
                "frames": {"frame.png": {"reviewed": True, "split": "test"}},
            },
        )
        benchmark = post("/api/benchmark", {})
        request = urllib.request.Request(  # noqa: S310
            base + "/api/images?name=frame.png", method="DELETE"
        )
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(request)  # noqa: S310
        confidence_floor = 0.9
        result = post(
            "/api/compare",
            {
                "benchmark": benchmark["path"],
                "models": [str(tmp_path / "first.pt")],
                "imgsz": 1024,
                "max_det": 1000,
                "device": "3",
                "confidence": confidence_floor,
            },
        )
        assert result["reports"][0]["tp"] == 1
        assert result["reports"][0]["fp"] == 0
        assert result["reports"][0]["fn"] == 0
        assert all(
            point["confidence"] >= confidence_floor
            for point in result["reports"][0]["threshold_curve"]
        )
        assert result["reports"][0]["capped_frames"] == []
        assert result["reports"][0]["per_frame"][0]["name"] == "frame.png"
        assert settings[0]["imgsz"] == 1024  # noqa: PLR2004
        assert settings[0]["max_det"] == 1000  # noqa: PLR2004
        assert settings[0]["device"] == "3"
        report_dir = workspace.root / "comparisons" / result["id"]
        assert (report_dir / "index.html").is_file()
        assert (report_dir / "frame_00_model_00.jpg").is_file()
        assert (report_dir / "f1_curve.svg").is_file()
        (tmp_path / "first.pt").write_bytes(b"changed checkpoint")
        with urllib.request.urlopen(  # noqa: S310
            base + f"/api/comparison?id={result['id']}&status=1"
        ) as response:
            status = json.loads(response.read())
        assert "Checkpoint changed" in status["input_warnings"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
