from __future__ import annotations

import io
import random
import sys
import types
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

if "ultralytics" not in sys.modules:
    ultralytics_stub = types.ModuleType("ultralytics")

    class _DummyYOLO:  # pragma: no cover - simple stub for imports
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def predict(self, *args: object, **kwargs: object) -> list[object]:
            return []

        def train(self, *args: object, **kwargs: object) -> None:
            return None

        def val(self, *args: object, **kwargs: object) -> None:
            return None

    ultralytics_stub.YOLO = _DummyYOLO

    utils_module = types.ModuleType("ultralytics.utils")
    metrics_module = types.ModuleType("ultralytics.utils.metrics")
    metrics_module.DetMetrics = type("DetMetrics", (), {})
    utils_module.metrics = metrics_module

    engine_module = types.ModuleType("ultralytics.engine")
    results_module = types.ModuleType("ultralytics.engine.results")
    results_module.Results = type("Results", (), {})
    engine_module.results = results_module

    sys.modules["ultralytics"] = ultralytics_stub
    sys.modules["ultralytics.utils"] = utils_module
    sys.modules["ultralytics.utils.metrics"] = metrics_module
    sys.modules["ultralytics.engine"] = engine_module
    sys.modules["ultralytics.engine.results"] = results_module

from dynsight._internal.vision.synthetic import (
    build_crops,
    synthesize_dataset_zip,
)


def _create_test_images() -> dict[str, Image.Image]:
    base = Image.new("RGB", (32, 32), color="white")
    draw = ImageDraw.Draw(base)
    draw.rectangle((4, 4, 14, 14), fill="red")
    draw.rectangle((18, 18, 28, 28), fill="blue")

    second = Image.new("RGB", (32, 32), color="white")
    draw2 = ImageDraw.Draw(second)
    draw2.ellipse((8, 8, 20, 20), fill="green")

    return {"first.jpg": base, "second.jpg": second}


def test_build_crops_extracts_regions() -> None:
    images = _create_test_images()
    annotations = {
        "first.jpg": [
            {"label": "red", "left": 4, "top": 4, "width": 10, "height": 10},
            {"label": "blue", "left": 18, "top": 18, "width": 10, "height": 10},
        ],
        "second.jpg": [
            {"label": "green", "left": 8, "top": 8, "width": 12, "height": 12},
        ],
    }

    crops, label_map = build_crops(images, annotations)

    assert len(crops) == 3
    assert set(label_map.keys()) == {"red", "blue", "green"}
    assert label_map["red"] == 0


def test_synthesize_dataset_generates_archive() -> None:
    images = _create_test_images()
    annotations = {
        "first.jpg": [
            {"label": "red", "left": 4, "top": 4, "width": 10, "height": 10},
        ],
        "second.jpg": [
            {"label": "green", "left": 8, "top": 8, "width": 12, "height": 12},
        ],
    }

    crops, label_map = build_crops(images, annotations)
    archive = synthesize_dataset_zip(
        crops,
        label_map,
        num_images=2,
        width=32,
        height=32,
        min_objects=1,
        max_objects=1,
        rng=random.Random(0),
    )

    with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
        members = set(zf.namelist())
        assert "dataset.yaml" in members
        assert "images/train/synt_0.jpg" in members
        assert "images/val/synt_1.jpg" in members

        label_data = zf.read("labels/train/synt_0.txt").decode().strip()
        first_class = int(label_data.split()[0])
        assert first_class in label_map.values()

        yaml = zf.read("dataset.yaml").decode()
        assert "nc: 2" in yaml
        assert "'red'" in yaml and "'green'" in yaml
