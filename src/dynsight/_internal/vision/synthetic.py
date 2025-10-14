"""Utilities for generating synthetic YOLO datasets from labeled crops."""

from __future__ import annotations

from dataclasses import dataclass
import io
import math
import random
from typing import Iterable, Mapping
import zipfile

from PIL import Image


@dataclass(frozen=True)
class Crop:
    """A cropped object extracted from a labeled image."""

    label: str
    image: Image.Image


def build_crops(
    images: Mapping[str, Image.Image],
    annotations: Mapping[str, Iterable[Mapping[str, object]]],
) -> tuple[list[Crop], dict[str, int]]:
    """Build a list of :class:`Crop` objects from annotations.

    Parameters
    ----------
    images
        Mapping between image file names and :class:`~PIL.Image.Image` objects.
    annotations
        Mapping of image file names to an iterable of annotation dictionaries.
        Each annotation must contain the keys ``label``, ``left``, ``top``,
        ``width`` and ``height``.

    Returns
    -------
    tuple[list[Crop], dict[str, int]]
        A tuple containing the extracted crops and the corresponding label map
        assigning an integer identifier to every label.

    Raises
    ------
    ValueError
        If no valid annotations are found.
    """

    crops: list[Crop] = []
    label_map: dict[str, int] = {}

    for image_name, boxes in annotations.items():
        image = images.get(image_name)
        if image is None:
            continue

        width, height = image.size
        for box in boxes:
            label = box.get("label")
            if not isinstance(label, str) or not label:
                continue

            try:
                left = int(round(float(box.get("left", 0))))
                top = int(round(float(box.get("top", 0))))
                box_width = int(round(float(box.get("width", 0))))
                box_height = int(round(float(box.get("height", 0))))
            except (TypeError, ValueError):
                continue

            if box_width <= 0 or box_height <= 0:
                continue

            left = max(0, min(left, width - 1))
            top = max(0, min(top, height - 1))
            right = max(left + 1, min(left + box_width, width))
            bottom = max(top + 1, min(top + box_height, height))

            if right <= left or bottom <= top:
                continue

            crop_img = image.crop((left, top, right, bottom)).convert("RGB")
            if crop_img.width == 0 or crop_img.height == 0:
                continue

            if label not in label_map:
                label_map[label] = len(label_map)

            crops.append(Crop(label=label, image=crop_img))

    if not crops:
        msg = "No valid annotations found."
        raise ValueError(msg)

    return crops, label_map


def _overlaps(
    x: int,
    y: int,
    w: int,
    h: int,
    placements: list[tuple[str, int, int, int, int]],
) -> bool:
    for _, px, py, pw, ph in placements:
        if not (x + w <= px or x >= px + pw or y + h <= py or y >= py + ph):
            return True
    return False


def synthesize_dataset_zip(
    crops: Iterable[Crop],
    label_map: Mapping[str, int],
    *,
    num_images: int,
    width: int,
    height: int,
    min_objects: int,
    max_objects: int,
    train_split: float = 0.8,
    rng: random.Random | None = None,
) -> bytes:
    """Generate a synthetic YOLO dataset archive from the given crops.

    Parameters
    ----------
    crops
        Iterable of :class:`Crop` instances that can be pasted on the generated
        canvases.
    label_map
        Mapping from the crop labels to the YOLO class indices.
    num_images
        Total number of images to synthesize.
    width, height
        Dimensions of the generated images.
    min_objects, max_objects
        Minimum and maximum number of objects per image.
    train_split
        Fraction of images that will be placed in the training subset.
    rng
        Optional pseudo-random number generator used for reproducibility in
        testing.

    Returns
    -------
    bytes
        The binary content of a ``.zip`` archive containing the synthetic
        dataset.

    Raises
    ------
    ValueError
        If the input parameters are invalid or if no crop can fit the desired
        canvas size.
    """

    if num_images <= 0:
        msg = "'num_images' must be a positive integer."
        raise ValueError(msg)
    if width <= 0 or height <= 0:
        msg = "Image width and height must be positive integers."
        raise ValueError(msg)
    if min_objects <= 0 or max_objects < min_objects:
        msg = "Invalid range for the number of objects per image."
        raise ValueError(msg)

    rng = rng or random.Random()
    crop_list = [crop for crop in crops if crop.image.width <= width and crop.image.height <= height]
    if not crop_list:
        msg = "No crops can fit within the requested canvas size."
        raise ValueError(msg)

    num_train = max(0, min(num_images, math.floor(num_images * train_split)))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx in range(num_images):
            placements: list[tuple[str, int, int, int, int]] = []
            canvas = Image.new("RGB", (width, height), color="white")
            target = rng.randint(min_objects, max_objects)

            for _ in range(target):
                crop = rng.choice(crop_list)
                cw, ch = crop.image.size
                max_x = width - cw
                max_y = height - ch
                if max_x < 0 or max_y < 0:
                    continue

                placed = False
                for _ in range(50):
                    x = rng.randint(0, max_x) if max_x > 0 else 0
                    y = rng.randint(0, max_y) if max_y > 0 else 0
                    if not _overlaps(x, y, cw, ch, placements):
                        placements.append((crop.label, x, y, cw, ch))
                        canvas.paste(crop.image, (x, y))
                        placed = True
                        break

                if not placed:
                    continue

            subset = "train" if idx < num_train else "val"
            img_name = f"images/{subset}/synt_{idx}.jpg"
            lbl_name = f"labels/{subset}/synt_{idx}.txt"

            img_buffer = io.BytesIO()
            canvas.save(img_buffer, format="JPEG", quality=95)
            zf.writestr(img_name, img_buffer.getvalue())

            lines = []
            for label, x, y, cw, ch in placements:
                cls = label_map[label]
                cx = (x + cw / 2.0) / width
                cy = (y + ch / 2.0) / height
                ww = cw / width
                hh = ch / height
                lines.append(
                    f"{cls} {cx:.6f} {cy:.6f} {ww:.6f} {hh:.6f}",
                )

            zf.writestr(lbl_name, "\n".join(lines))

        names = list(label_map.keys())
        names_str = ", ".join(f"'{name}'" for name in names)
        yaml_content = (
            "path: .\n"
            "train: images/train\n"
            "val: images/val\n"
            f"nc: {len(names)}\n"
            f"names: [{names_str}]\n"
        )
        zf.writestr("dataset.yaml", yaml_content)

    return buffer.getvalue()
