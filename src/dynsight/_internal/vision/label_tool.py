"""Local web application for building YOLO training datasets.

The tool starts a small HTTP server (standard library only) that serves
a single-page labeling GUI and a JSON API. Images (or video frames) are
stored inside a *workspace* directory, while the labeling session
(labels and boxes) is kept in memory and written to disk only when the
user explicitly saves it to a chosen file. Datasets are written
directly to disk in the exact layout expected by
:class:`dynsight.vision.VisionInstance`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import shutil
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast
from urllib.parse import parse_qs, urlparse

import yaml
from PIL import Image

from dynsight._internal.vision.review import (
    benchmark_snapshot,
    evaluate_predictions,
)

if TYPE_CHECKING:
    from ultralytics.engine.results import Results

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "label_tool"
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/logo.png": ("logo.png", "image/png"),
}

_ProgressCallback = Callable[[int, int], None]

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
_VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
_BROWSE_SUFFIXES = {
    "session": {".json"},
    "model": {".pt"},
    "benchmark": {".json"},
    "image": _IMAGE_SUFFIXES,
    "video": _VIDEO_SUFFIXES,
}
_MAX_BROWSE_ENTRIES = 500
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

_MIN_SPLIT_IMAGES = 2
_MAX_PLACEMENT_TRIES = 50


def _safe_name(raw: str) -> str:
    """Reduce a client-provided file name to a safe basename."""
    name = _UNSAFE_CHARS.sub("_", Path(raw).name)
    if not re.search(r"[A-Za-z0-9]", Path(name).stem):
        msg = f"Invalid file name: '{raw}'"
        raise ValueError(msg)
    return name


def _browse_directory(
    raw_path: str, kind: str, workspace: Path
) -> dict[str, Any]:
    """List one server-side directory for the read-only path picker."""
    if kind not in _BROWSE_SUFFIXES:
        msg = "Unsupported file type."
        raise ValueError(msg)
    path = Path(raw_path).expanduser().resolve() if raw_path else workspace
    if not path.is_dir():
        msg = f"Directory not found: '{path}'"
        raise ValueError(msg)
    entries = []
    try:
        children = sorted(
            path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        )
        for child in children:
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entries.append(
                        {
                            "name": child.name,
                            "path": str(child),
                            "is_dir": True,
                        }
                    )
                elif (
                    child.is_file()
                    and child.suffix.lower() in _BROWSE_SUFFIXES[kind]
                ):
                    entries.append(
                        {
                            "name": child.name,
                            "path": str(child),
                            "is_dir": False,
                        }
                    )
            except OSError:
                continue
            if len(entries) >= _MAX_BROWSE_ENTRIES:
                break
    except PermissionError as e:
        msg = f"Cannot read directory: '{path}'"
        raise ValueError(msg) from e
    return {"path": str(path), "parent": str(path.parent), "entries": entries}


def _source_file(raw_path: object, suffixes: set[str]) -> Path:
    """Resolve a selected server-side file and check its expected type."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        msg = "A source file path is required."
        raise ValueError(msg)
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in suffixes:
        msg = f"Unsupported or missing source file: '{path}'"
        raise ValueError(msg)
    return path


def _image_size(path: Path) -> tuple[int, int]:
    """Return (width, height) of an image without loading pixel data."""
    with Image.open(path) as img:
        return img.size


def _empty_session() -> dict[str, Any]:
    """Return a new empty labeling session."""
    return {"schema_version": 2, "labels": [], "annotations": {}, "frames": {}}


def _normalize_session_path(raw: object) -> Path:
    """Validate and normalize a user-provided session file path."""
    if not raw or not str(raw).strip():
        msg = "A file path is required for the session."
        raise ValueError(msg)
    path = Path(str(raw).strip()).expanduser()
    if path.is_dir():
        path = path / "session.json"
    elif path.suffix.lower() != ".json":
        path = path.with_name(path.name + ".json")
    return path


def save_session_file(session: dict[str, Any], raw_path: object) -> Path:
    """Write a labeling session to an explicitly chosen file."""
    path = _normalize_session_path(raw_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(session, f, indent=1)
    return path


def load_session_file(raw_path: object) -> dict[str, Any]:
    """Read a labeling session from a file."""
    path = _normalize_session_path(raw_path)
    if not path.is_file():
        msg = f"Session file not found: '{path}'"
        raise ValueError(msg)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        msg = f"'{path}' is not a valid session file."
        raise TypeError(msg)
    return {
        "schema_version": 2,
        "labels": data.get("labels", []),
        "annotations": data.get("annotations", {}),
        "frames": data.get("frames", {}),
    }


class _Workspace:
    """Filesystem-backed image storage of a labeling session."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.images_dir = self.root / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def list_images(self) -> list[dict[str, Any]]:
        """Return metadata for every image stored in the workspace."""
        infos = []
        for path in sorted(self.images_dir.iterdir()):
            if path.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            width, height = _image_size(path)
            infos.append({"name": path.name, "width": width, "height": height})
        return infos

    def add_image(self, name: str, data: bytes) -> dict[str, Any]:
        """Store an uploaded image after validating it."""
        safe = _safe_name(name)
        if Path(safe).suffix.lower() not in _IMAGE_SUFFIXES:
            msg = f"Unsupported image format: '{safe}'"
            raise ValueError(msg)
        dst = self.images_dir / safe
        dst.write_bytes(data)
        try:
            width, height = _image_size(dst)
        # Broad catch: PIL.Image.open may be monkey-patched by other
        # libraries (e.g. ultralytics) and raise unexpected errors.
        except Exception:  # noqa: BLE001
            dst.unlink(missing_ok=True)
            msg = f"'{safe}' is not a readable image."
            raise ValueError(msg) from None
        return {"name": safe, "width": width, "height": height}

    def add_video(
        self,
        name: str,
        data: bytes,
        stride: int,
        on_progress: _ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        """Extract frames from an uploaded video into the workspace."""
        safe = _safe_name(name)
        if Path(safe).suffix.lower() not in _VIDEO_SUFFIXES:
            msg = f"Unsupported video format: '{safe}'"
            raise ValueError(msg)
        tmp = self.root / f"_upload_{safe}"
        tmp.write_bytes(data)
        try:
            return self.add_video_from_path(safe, tmp, stride, on_progress)
        finally:
            tmp.unlink(missing_ok=True)

    def add_video_from_path(
        self,
        name: str,
        path: Path,
        stride: int,
        on_progress: _ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        """Extract frames directly from a video already on the server."""
        import cv2  # heavy import, only needed here

        safe = _safe_name(name)
        if Path(safe).suffix.lower() not in _VIDEO_SUFFIXES:
            msg = f"Unsupported video format: '{safe}'"
            raise ValueError(msg)
        stride = max(1, stride)
        stem = Path(safe).stem
        frames: list[dict[str, Any]] = []
        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                msg = f"Could not open video '{safe}'."
                raise ValueError(msg)
            total = max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
            index = 0
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                if index % stride == 0:
                    frame_name = f"{stem}_{index:06d}.jpg"
                    cv2.imwrite(str(self.images_dir / frame_name), frame)
                    height, width = frame.shape[:2]
                    frames.append(
                        {
                            "name": frame_name,
                            "width": int(width),
                            "height": int(height),
                            "source": safe,
                            "frame_index": index,
                            "timestamp_ms": capture.get(cv2.CAP_PROP_POS_MSEC),
                        }
                    )
                index += 1
                if on_progress is not None:
                    on_progress(index, total)
        finally:
            capture.release()
        if not frames:
            msg = f"No frames could be extracted from '{safe}'."
            raise ValueError(msg)
        return frames

    def delete_image(self, name: str) -> None:
        """Remove an image from the workspace."""
        (self.images_dir / _safe_name(name)).unlink(missing_ok=True)


def _yolo_lines(
    boxes: list[dict[str, Any]],
    class_ids: dict[str, int],
    width: int,
    height: int,
) -> str:
    """Convert pixel-space boxes to YOLO txt content."""
    lines = []
    for box in boxes:
        label = box["label"]
        if label not in class_ids:
            continue
        cx = (box["x"] + box["w"] / 2) / width
        cy = (box["y"] + box["h"] / 2) / height
        w = box["w"] / width
        h = box["h"] / height
        cx, cy = min(max(cx, 0.0), 1.0), min(max(cy, 0.0), 1.0)
        w, h = min(max(w, 0.0), 1.0), min(max(h, 0.0), 1.0)
        lines.append(f"{class_ids[label]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    return "".join(f"{line}\n" for line in lines)


def _dataset_dirs(dataset_path: Path) -> dict[str, Path]:
    """Create and return the YOLO dataset directory layout."""
    if dataset_path.exists() and any(dataset_path.iterdir()):
        msg = f"Dataset directory already contains files: {dataset_path}"
        raise ValueError(msg)
    dirs = {
        "images/train": dataset_path / "images" / "train",
        "images/val": dataset_path / "images" / "val",
        "labels/train": dataset_path / "labels" / "train",
        "labels/val": dataset_path / "labels" / "val",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def _frozen_frames(workspace: _Workspace) -> list[dict[str, Any]]:
    """Find source frames referenced by saved benchmark snapshots."""
    frames = []
    for path in (workspace.root / "benchmarks").glob("*.json"):
        if path.name.startswith("comparison_"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") == 1:
            frames.extend(payload.get("frames", []))
    return frames


def _write_dataset_yaml(dataset_path: Path, names: list[str]) -> Path:
    """Write the dataset.yaml file consumed by ``set_training_dataset``."""
    yaml_path = dataset_path / "dataset.yaml"
    content = {
        "path": str(dataset_path.resolve()),
        "train": "images/train",
        "val": "images/val",
        "nc": len(names),
        "names": names,
    }
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(content, f, sort_keys=False)
    return yaml_path


def _split_count(total: int, train_split: float) -> int:
    """Number of training items for a given split fraction."""
    num_train = round(total * train_split)
    if total >= _MIN_SPLIT_IMAGES:
        num_train = min(max(num_train, 1), total - 1)
    return num_train


def export_dataset(
    workspace: _Workspace,
    session: dict[str, Any],
    name: str,
    train_split: float = 0.8,
    shuffle: bool = True,
    seed: int | None = None,
    output_dir: Path | None = None,
    on_progress: _ProgressCallback | None = None,
) -> dict[str, Any]:
    """Write a YOLO dataset from the current session to disk.

    Class IDs follow the order of the session label list, so they are
    stable across exports. Every image receives a label file (empty if
    it has no annotations) and every known class appears in
    ``dataset.yaml`` even when unused.
    """
    if not 0.0 < train_split < 1.0:
        msg = "train_split must be between 0 and 1."
        raise ValueError(msg)
    images = [
        info
        for info in workspace.list_images()
        if session.get("frames", {}).get(info["name"], {}).get("reviewed")
        and session["frames"][info["name"]].get("split") != "test"
    ]
    if not images:
        msg = "No reviewed non-test images in the workspace."
        raise ValueError(msg)
    names = [label["name"] for label in session.get("labels", [])]
    if not names:
        msg = "No labels defined."
        raise ValueError(msg)
    class_ids = {label: idx for idx, label in enumerate(names)}
    annotations: dict[str, Any] = session.get("annotations", {})

    base = output_dir if output_dir is not None else workspace.root
    dataset_path = (base / _safe_name(name)).resolve()
    dirs = _dataset_dirs(dataset_path)

    groups: dict[str, list[dict[str, Any]]] = {}
    for info in images:
        source = session["frames"][info["name"]].get("source", info["name"])
        groups.setdefault(source, []).append(info)
    group_names = sorted(groups)
    if shuffle:
        random.Random(seed).shuffle(group_names)  # noqa: S311
    if len(group_names) > 1:
        train_groups = set(
            group_names[: _split_count(len(group_names), train_split)]
        )
        assignments = {
            info["name"]: "train" if source in train_groups else "val"
            for source, members in groups.items()
            for info in members
        }
        split_policy = "grouped_by_source"
    else:
        num_train = _split_count(len(images), train_split)
        assignments = {
            info["name"]: "train" if idx < num_train else "val"
            for idx, info in enumerate(images)
        }
        split_policy = "single_source_frame_split_correlated"

    for idx, info in enumerate(images):
        subset = assignments[info["name"]]
        src = workspace.images_dir / info["name"]
        shutil.copy2(src, dirs[f"images/{subset}"] / info["name"])
        txt = _yolo_lines(
            annotations.get(info["name"], []),
            class_ids,
            info["width"],
            info["height"],
        )
        lbl = dirs[f"labels/{subset}"] / (Path(info["name"]).stem + ".txt")
        lbl.write_text(txt, encoding="utf-8")
        if on_progress is not None:
            on_progress(idx + 1, len(images))

    yaml_path = _write_dataset_yaml(dataset_path, names)
    num_train = sum(split == "train" for split in assignments.values())
    (dataset_path / "frame_manifest.json").write_text(
        json.dumps(
            {
                "split_policy": split_policy,
                "frames": [
                    {
                        "name": info["name"],
                        "source": session["frames"][info["name"]].get(
                            "source", info["name"]
                        ),
                        "frame_index": session["frames"][info["name"]].get(
                            "frame_index"
                        ),
                        "timestamp_ms": session["frames"][info["name"]].get(
                            "timestamp_ms"
                        ),
                        "split": assignments[info["name"]],
                    }
                    for info in images
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "path": str(dataset_path),
        "yaml": str(yaml_path),
        "num_train": num_train,
        "num_val": len(images) - num_train,
        "split_policy": split_policy,
    }


def _place_crop(
    rng: random.Random,
    crop_size: tuple[int, int],
    canvas_size: tuple[int, int],
    placed: list[tuple[float, float, float, float]],
    scale_range: tuple[float, float],
) -> tuple[int, int, int, int] | None:
    """Find a non-overlapping position for a crop, or ``None``."""
    for _ in range(_MAX_PLACEMENT_TRIES):
        scale = rng.uniform(*scale_range)
        w = max(1, int(crop_size[0] * scale))
        h = max(1, int(crop_size[1] * scale))
        if w >= canvas_size[0] or h >= canvas_size[1]:
            continue
        x = rng.randint(0, canvas_size[0] - w)
        y = rng.randint(0, canvas_size[1] - h)
        overlap = any(
            x < px + pw and x + w > px and y < py + ph and y + h > py
            for px, py, pw, ph in placed
        )
        if not overlap:
            return x, y, w, h
    return None


def synthesize_dataset(
    workspace: _Workspace,
    session: dict[str, Any],
    name: str,
    num_images: int = 10,
    width: int = 640,
    height: int = 640,
    per_image: int = 10,
    train_split: float = 0.8,
    scale_range: tuple[float, float] = (1.0, 1.0),
    background: str = "#ffffff",
    seed: int | None = None,
    output_dir: Path | None = None,
    on_progress: _ProgressCallback | None = None,
) -> dict[str, Any]:
    """Generate a synthetic YOLO dataset from the annotated crops.

    Annotated regions are cut out of the source images and pasted at
    random non-overlapping positions onto uniform background canvases.
    """
    names = [label["name"] for label in session.get("labels", [])]
    class_ids = {label: idx for idx, label in enumerate(names)}
    annotations: dict[str, Any] = session.get("annotations", {})
    crops = [
        {"image": image_name, **box}
        for image_name, boxes in annotations.items()
        for box in boxes
        if box["label"] in class_ids
        and session.get("frames", {}).get(image_name, {}).get("reviewed")
        and session["frames"][image_name].get("split") != "test"
        and (workspace.images_dir / image_name).is_file()
    ]
    if not crops:
        msg = "No annotations available to synthesize from."
        raise ValueError(msg)

    base = output_dir if output_dir is not None else workspace.root
    dataset_path = (base / _safe_name(name)).resolve()
    dirs = _dataset_dirs(dataset_path)
    rng = random.Random(seed)  # noqa: S311
    num_train = _split_count(num_images, train_split)

    sources: dict[str, Image.Image] = {}
    for idx in range(num_images):
        canvas = Image.new("RGB", (width, height), background)
        placed: list[tuple[float, float, float, float]] = []
        boxes: list[dict[str, Any]] = []
        for _ in range(per_image):
            crop = rng.choice(crops)
            if crop["image"] not in sources:
                src_path = workspace.images_dir / crop["image"]
                sources[crop["image"]] = Image.open(src_path).convert("RGB")
            source = sources[crop["image"]]
            left, top = int(crop["x"]), int(crop["y"])
            cw = max(1, int(crop["w"]))
            ch = max(1, int(crop["h"]))
            patch = source.crop((left, top, left + cw, top + ch))
            spot = _place_crop(
                rng, (cw, ch), (width, height), placed, scale_range
            )
            if spot is None:
                continue
            x, y, w, h = spot
            canvas.paste(patch.resize((w, h)), (x, y))
            placed.append((x, y, w, h))
            boxes.append(
                {"label": crop["label"], "x": x, "y": y, "w": w, "h": h}
            )
        subset = "train" if idx < num_train else "val"
        canvas.save(dirs[f"images/{subset}"] / f"synt_{idx:05d}.jpg")
        txt = _yolo_lines(boxes, class_ids, width, height)
        lbl = dirs[f"labels/{subset}"] / f"synt_{idx:05d}.txt"
        lbl.write_text(txt, encoding="utf-8")
        if on_progress is not None:
            on_progress(idx + 1, num_images)

    for source in sources.values():
        source.close()
    yaml_path = _write_dataset_yaml(dataset_path, names)
    return {
        "path": str(dataset_path),
        "yaml": str(yaml_path),
        "num_train": num_train,
        "num_val": num_images - num_train,
    }


class _LabelToolServer(ThreadingHTTPServer):
    """HTTP server carrying the state shared by all requests.

    The labeling session (labels and boxes) lives in ``self.session``
    and is written to disk only through the explicit save endpoint.
    ``self.progress`` mirrors the state of the long-running operation
    currently in flight (if any) and is polled by the GUI to render
    progress bars.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, workspace: _Workspace) -> None:
        self.workspace = workspace
        self.session = _empty_session()
        self.session_path: str | None = None
        self.session_dirty = False
        self.progress: dict[str, Any] = {"active": False}
        self.progress_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), _RequestHandler)

    def start_progress(self, label: str) -> _ProgressCallback:
        """Mark a long operation as active and return its callback."""
        with self.progress_lock:
            self.progress = {
                "active": True,
                "label": label,
                "done": 0,
                "total": 0,
            }

        def on_progress(done: int, total: int) -> None:
            with self.progress_lock:
                if self.progress.get("active"):
                    self.progress["done"] = done
                    self.progress["total"] = total

        return on_progress

    def end_progress(self) -> None:
        """Mark the current long operation as finished."""
        with self.progress_lock:
            self.progress = {"active": False}


class _RequestHandler(BaseHTTPRequestHandler):
    """Routes static files and the JSON API."""

    server: _LabelToolServer

    def log_message(self, fmt: str, *args: object) -> None:
        """Silence default request logging."""

    @property
    def _workspace(self) -> _Workspace:
        return self.server.workspace

    def _send_json(
        self, payload: dict[str, Any], status: int = HTTPStatus.OK
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(
        self, path: Path, content_type: str, cache_control: str = "no-store"
    ) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _query(self) -> dict[str, str]:
        parsed = parse_qs(urlparse(self.path).query)
        return {key: values[0] for key, values in parsed.items()}

    def do_GET(self) -> None:  # noqa: N802
        """Serve the GUI, workspace images and the state endpoint."""
        route = urlparse(self.path).path
        if route in _STATIC_FILES:
            file_name, content_type = _STATIC_FILES[route]
            self._send_file(_STATIC_DIR / file_name, content_type)
        elif route.startswith("/images/"):
            name = _safe_name(route[len("/images/") :])
            suffix = Path(name).suffix.lower().lstrip(".")
            content_type = f"image/{'jpeg' if suffix == 'jpg' else suffix}"
            self._send_file(
                self._workspace.images_dir / name,
                content_type,
                cache_control="max-age=300",
            )
        elif route == "/api/state":
            self._api(self._handle_state)
        elif route == "/api/progress":
            self._api(self._handle_progress)
        elif route == "/api/files":
            self._api(self._handle_files)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        """Dispatch API mutations."""
        route = urlparse(self.path).path
        handlers = {
            "/api/sync": self._handle_sync,
            "/api/session": self._handle_save_session,
            "/api/session/load": self._handle_load_session,
            "/api/images": self._handle_upload_image,
            "/api/images/from-path": self._handle_import_image,
            "/api/video": self._handle_upload_video,
            "/api/video/from-path": self._handle_import_video,
            "/api/export": self._handle_export,
            "/api/predict": self._handle_predict,
            "/api/benchmark": self._handle_benchmark,
            "/api/compare": self._handle_compare,
            "/api/synthesize": self._handle_synthesize,
            "/api/shutdown": self._handle_shutdown,
        }
        handler = handlers.get(route)
        if handler is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._api(handler)

    def do_DELETE(self) -> None:  # noqa: N802
        """Delete a workspace image."""
        route = urlparse(self.path).path
        if route == "/api/images":
            self._api(self._handle_delete_image)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _api(self, handler: Any) -> None:
        try:
            payload = handler()
        except (ValueError, KeyError, TypeError, OSError) as e:
            logger.warning(f"Request failed: {e}")
            self._send_json({"error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        else:
            self._send_json(payload)

    def _json_body(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self._read_body() or b"{}")
        return data

    def _session_from(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "labels": body.get("labels", []),
            "annotations": body.get("annotations", {}),
            "frames": body.get("frames", {}),
        }

    def _handle_state(self) -> dict[str, Any]:
        session = self.server.session
        return {
            "workspace": str(self._workspace.root),
            "images": self._workspace.list_images(),
            "labels": session.get("labels", []),
            "annotations": session.get("annotations", {}),
            "frames": session.get("frames", {}),
            "session_path": self.server.session_path,
            "dirty": self.server.session_dirty,
        }

    def _handle_progress(self) -> dict[str, Any]:
        with self.server.progress_lock:
            return dict(self.server.progress)

    def _handle_files(self) -> dict[str, Any]:
        query = self._query()
        return _browse_directory(
            query.get("path", ""), query.get("kind", ""), self._workspace.root
        )

    def _handle_sync(self) -> dict[str, Any]:
        """Update the in-memory session (no disk write)."""
        self.server.session = self._session_from(self._json_body())
        self.server.session_dirty = True
        return {"synced": True}

    def _handle_save_session(self) -> dict[str, Any]:
        """Write the session to an explicitly chosen file."""
        body = self._json_body()
        if "labels" in body or "annotations" in body or "frames" in body:
            self.server.session = self._session_from(body)
        path = save_session_file(self.server.session, body.get("path"))
        self.server.session_path = str(path)
        self.server.session_dirty = False
        return {"path": str(path)}

    def _handle_load_session(self) -> dict[str, Any]:
        """Load a session file into memory and return it."""
        body = self._json_body()
        session = load_session_file(body.get("path"))
        self.server.session = session
        self.server.session_path = str(
            _normalize_session_path(body.get("path"))
        )
        self.server.session_dirty = False
        return session

    def _handle_upload_image(self) -> dict[str, Any]:
        query = self._query()
        name = _safe_name(query["name"])
        self._check_image_replacement(name)
        return self._workspace.add_image(name, self._read_body())

    def _check_image_replacement(self, name: str) -> None:
        if any(
            frame["name"] == name for frame in _frozen_frames(self._workspace)
        ):
            msg = "This image belongs to a frozen benchmark."
            raise ValueError(msg)
        if self.server.session["frames"].get(name, {}).get("split") == "test":
            msg = "Remove this image from the test set before replacing it."
            raise ValueError(msg)

    def _handle_import_image(self) -> dict[str, Any]:
        path = _source_file(self._json_body().get("path"), _IMAGE_SUFFIXES)
        name = _safe_name(path.name)
        self._check_image_replacement(name)
        return self._workspace.add_image(name, path.read_bytes())

    def _handle_upload_video(self) -> dict[str, Any]:
        query = self._query()
        name = _safe_name(query["name"])
        self._check_video_replacement(name)
        data = self._read_body()
        on_progress = self.server.start_progress("Extracting frames")
        try:
            frames = self._workspace.add_video(
                name,
                data,
                stride=int(query.get("stride", "1")),
                on_progress=on_progress,
            )
        finally:
            self.server.end_progress()
        return {"frames": frames}

    def _check_video_replacement(self, name: str) -> None:
        frame_prefix = f"{Path(name).stem}_"
        if any(
            frame.get("source") == name
            or frame["name"].startswith(frame_prefix)
            for frame in _frozen_frames(self._workspace)
        ):
            msg = "This video has frames in a frozen benchmark."
            raise ValueError(msg)
        if any(
            (meta.get("source") == name or frame_name.startswith(frame_prefix))
            and meta.get("split") == "test"
            for frame_name, meta in self.server.session["frames"].items()
        ):
            msg = "Remove test frames from this video before replacing it."
            raise ValueError(msg)

    def _handle_import_video(self) -> dict[str, Any]:
        body = self._json_body()
        path = _source_file(body.get("path"), _VIDEO_SUFFIXES)
        name = _safe_name(path.name)
        self._check_video_replacement(name)
        on_progress = self.server.start_progress("Extracting frames")
        try:
            frames = self._workspace.add_video_from_path(
                name,
                path,
                stride=int(body.get("stride", 1)),
                on_progress=on_progress,
            )
        finally:
            self.server.end_progress()
        return {"frames": frames}

    def _handle_delete_image(self) -> dict[str, Any]:
        query = self._query()
        name = _safe_name(query["name"])
        if any(
            frame["name"] == name for frame in _frozen_frames(self._workspace)
        ):
            msg = "This image belongs to a frozen benchmark."
            raise ValueError(msg)
        if self.server.session["frames"].get(name, {}).get("split") == "test":
            msg = "Remove this image from the test set before deleting it."
            raise ValueError(msg)
        self._workspace.delete_image(name)
        self.server.session["annotations"].pop(name, None)
        self.server.session["frames"].pop(name, None)
        return {"deleted": True}

    def _handle_predict(self) -> dict[str, Any]:
        """Import model boxes as editable, unreviewed drafts."""
        from ultralytics import YOLO

        body = self._json_body()
        model_path = str(body.get("model", "")).strip()
        if not model_path:
            msg = "A model path is required."
            raise ValueError(msg)
        model = YOLO(model_path)
        names = model.names
        labels = self.server.session["labels"]
        if not labels:
            self.server.session["labels"] = [
                {"name": str(names[i]), "color": "#3b82f6"}
                for i in sorted(names)
            ]
            labels = self.server.session["labels"]
        elif [label["name"] for label in labels] != [
            str(names[i]) for i in sorted(names)
        ]:
            msg = "Model classes must match the session labels in order."
            raise ValueError(msg)
        predictions = {}
        for info in self._workspace.list_images():
            name = info["name"]
            meta = self.server.session["frames"].setdefault(name, {})
            if meta.get("reviewed") or meta.get("split") == "test":
                continue
            result = cast(
                "list[Results]",
                model.predict(
                    str(self._workspace.images_dir / name),
                    conf=float(body.get("confidence", 0.25)),
                    iou=float(body.get("nms_iou", 0.7)),
                    imgsz=int(body.get("imgsz", 640)),
                    max_det=int(body.get("max_det", 500)),
                    device=body.get("device") or None,
                    verbose=False,
                ),
            )[0]
            boxes = []
            box_data = result.boxes
            rows = (
                zip(
                    box_data.xyxy.tolist(),
                    box_data.cls.tolist(),
                    box_data.conf.tolist(),
                )
                if box_data is not None
                else []
            )
            for xyxy, cls, conf in rows:
                x1, y1, x2, y2 = xyxy
                boxes.append(
                    {
                        "label": str(names[int(cls)]),
                        "x": x1,
                        "y": y1,
                        "w": x2 - x1,
                        "h": y2 - y1,
                        "confidence": conf,
                        "model": model_path,
                        "original_box": [x1, y1, x2, y2],
                    }
                )
            predictions[name] = boxes
            meta["reviewed"] = False
            meta["prediction_model"] = model_path
            self.server.session["annotations"][name] = boxes
        self.server.session_dirty = True
        return {
            "predictions": predictions,
            "frames": self.server.session["frames"],
        }

    def _handle_benchmark(self) -> dict[str, Any]:
        snapshot = benchmark_snapshot(
            self._workspace.images_dir, self.server.session
        )
        path = (
            self._workspace.root
            / "benchmarks"
            / f"{snapshot['benchmark_id']}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        return {
            "path": str(path),
            "benchmark_id": snapshot["benchmark_id"],
            "frames": len(snapshot["frames"]),
        }

    def _handle_compare(self) -> dict[str, Any]:
        from ultralytics import YOLO

        body = self._json_body()
        path = Path(str(body.get("benchmark", "")))
        if (
            not path.is_file()
            or path.parent.resolve()
            != (self._workspace.root / "benchmarks").resolve()
        ):
            msg = "Select a benchmark frozen in this workspace."
            raise ValueError(msg)
        benchmark = json.loads(path.read_text(encoding="utf-8"))
        models = body.get("models", [])
        if not isinstance(models, list) or not models:
            msg = "At least one model path is required."
            raise ValueError(msg)
        reports = []
        for model_path in models:
            model = YOLO(str(model_path))
            predictions = {}
            for frame in benchmark["frames"]:
                image_path = self._workspace.images_dir / frame["name"]
                if (
                    hashlib.sha256(image_path.read_bytes()).hexdigest()
                    != frame["sha256"]
                ):
                    msg = f"Benchmark image changed: {frame['name']}"
                    raise ValueError(msg)
                result = cast(
                    "list[Results]",
                    model.predict(
                        str(image_path),
                        conf=float(body.get("confidence", 0.01)),
                        iou=float(body.get("nms_iou", 0.7)),
                        imgsz=int(body.get("imgsz", 640)),
                        max_det=int(body.get("max_det", 500)),
                        device=body.get("device") or None,
                        verbose=False,
                    ),
                )[0]
                box_data = result.boxes
                rows = (
                    zip(
                        box_data.xyxy.tolist(),
                        box_data.cls.tolist(),
                        box_data.conf.tolist(),
                    )
                    if box_data is not None
                    else []
                )
                predictions[frame["name"]] = [
                    {
                        "label": str(model.names[int(cls)]),
                        "x": xyxy[0],
                        "y": xyxy[1],
                        "w": xyxy[2] - xyxy[0],
                        "h": xyxy[3] - xyxy[1],
                        "confidence": conf,
                    }
                    for xyxy, cls, conf in rows
                ]
            report = evaluate_predictions(
                benchmark, predictions, float(body.get("match_iou", 0.5))
            )
            confidence_floor = float(body.get("confidence", 0.01))
            report["threshold_curve"] = [
                point
                for point in report["threshold_curve"]
                if point["confidence"] >= confidence_floor
            ]
            report["capped_frames"] = [
                name
                for name, boxes in predictions.items()
                if len(boxes) >= int(body.get("max_det", 500))
            ]
            report["model"] = str(model_path)
            model_file = Path(str(model_path))
            report["model_sha256"] = (
                hashlib.sha256(model_file.read_bytes()).hexdigest()
                if model_file.is_file()
                else None
            )
            report["inference"] = {
                key: body.get(key)
                for key in (
                    "confidence",
                    "nms_iou",
                    "imgsz",
                    "max_det",
                    "device",
                )
            }
            reports.append(report)
        output = {"benchmark": str(path), "reports": reports}
        comparison_id = hashlib.sha256(
            json.dumps(
                {
                    "benchmark": benchmark["benchmark_id"],
                    "models": models,
                    "settings": body,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:12]
        report_path = (
            self._workspace.root
            / "benchmarks"
            / f"comparison_{benchmark['benchmark_id']}_{comparison_id}.json"
        )
        report_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        return {"path": str(report_path), **output}

    def _handle_export(self) -> dict[str, Any]:
        body = self._json_body()
        output = body.get("output_dir")
        on_progress = self.server.start_progress("Exporting dataset")
        try:
            return export_dataset(
                self._workspace,
                self.server.session,
                name=body.get("name", "yolo_dataset"),
                train_split=float(body.get("train_split", 0.8)),
                shuffle=bool(body.get("shuffle", True)),
                seed=body.get("seed"),
                output_dir=Path(output) if output else None,
                on_progress=on_progress,
            )
        finally:
            self.server.end_progress()

    def _handle_synthesize(self) -> dict[str, Any]:
        body = self._json_body()
        output = body.get("output_dir")
        on_progress = self.server.start_progress("Synthesizing dataset")
        try:
            return synthesize_dataset(
                self._workspace,
                self.server.session,
                name=body.get("name", "synt_dataset"),
                num_images=int(body.get("num_images", 10)),
                width=int(body.get("width", 640)),
                height=int(body.get("height", 640)),
                per_image=int(body.get("per_image", 10)),
                train_split=float(body.get("train_split", 0.8)),
                scale_range=(
                    float(body.get("scale_min", 1.0)),
                    float(body.get("scale_max", 1.0)),
                ),
                background=str(body.get("background", "#ffffff")),
                seed=body.get("seed"),
                output_dir=Path(output) if output else None,
                on_progress=on_progress,
            )
        finally:
            self.server.end_progress()

    def _handle_shutdown(self) -> dict[str, Any]:
        logger.info("Shutdown requested from the GUI.")
        threading.Thread(target=self.server.shutdown).start()
        return {"shutdown": True}


def label_tool(
    port: int = 8888,
    workspace: str | Path | None = None,
    open_browser: bool = True,
) -> None:
    """Start the dynsight labeling tool.

    The tool opens in the default web browser. Uploaded images are
    stored in ``workspace``, while the labeling session (labels and
    boxes) is saved to disk only when explicitly requested from the
    GUI, to a file path chosen by the user. The server stops with the
    *Quit* button in the GUI or with ``Ctrl+C`` in the terminal.

    Parameters:
        port:
            Port for the local HTTP server.

        workspace:
            Directory where images and exported datasets are stored by
            default. Defaults to ``./label_tool_workspace``.

        open_browser:
            Automatically open the GUI in the default browser.
    """
    root = (
        Path(workspace) if workspace else Path.cwd() / ("label_tool_workspace")
    )
    server = _LabelToolServer(port, _Workspace(root))
    url = f"http://127.0.0.1:{port}/"
    logger.info(f"Labeling tool running at {url}")
    logger.info(f"Workspace: {root.resolve()}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Interrupted.")
    finally:
        server.server_close()
        logger.info("Server closed.")
