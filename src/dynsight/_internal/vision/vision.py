from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib

import cv2
from ultralytics import YOLO

from .label_creator import create_dataset


def extract_frames(
    video_path: pathlib.Path,
    output_path: pathlib.Path,
    start_frame: int | None = None,
    end_frame: int | None = None,
) -> None:
    """Extract frames from a video file.

    Parameters:
        video_path:
            Path to the input video file.
        output_path:
            Path to save the extracted frames.
        start_frame:
            Frame number to start extraction from. If None, extraction starts
            from the beginning.
        end_frame:
            Frame number to stop extraction at. If None, extraction continues
            until the end of the video.
    """
    output_path.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(video_path)
    n_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if end_frame is None:
        end_frame = n_frames
    if start_frame is None:
        start_frame = 0
    if start_frame < 0 or end_frame > n_frames or start_frame > end_frame:
        capture.release()
        error_message = f"Invalid frame range ({start_frame} - {end_frame})"
        raise ValueError(error_message)
    for i in range(start_frame, end_frame):
        index = i - start_frame
        capture.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = capture.read()
        if not ret:
            break
        cv2.imwrite(str(output_path / f"{index}.png"), frame)


def train_model(
    train_img_path: pathlib.Path,
    val_img_path: pathlib.Path,
    yaml_file: pathlib.Path,
) -> None:
    create_dataset(train_img_path, val_img_path)

    # Initial training
    model = YOLO("yolo12x.pt")
    model.train(
        data=yaml_file,  # Path to the dataset configuration file
        epochs=100,  # Number of training epochs
        imgsz=680,  # Image size
        batch=2,  # Batch size (adjust based on GPU memory)
        optimizer="auto",  # Use Stochastic Gradient Descent (try 'Adam' too)
        augment=False,  # Enable augmentations
        fliplr=1.0,  # Horizontal flip probability
        flipud=1.0,  # Vertical flip probability
        hsv_h=0.015,  # Adjust hue
        hsv_s=0.7,  # Adjust saturation
        hsv_v=0.4,  # Adjust brightness
        mosaic=1.0,  # Enable mosaic augmentation
        mixup=1.0,  # MixUp augmentation
        device=[2, 3],  # Use multiple GPUs
        patience=10,  # Early stopping patience
        workers=16,  # Number of workers for data loading
        name="pretrained_model",  # Name for the training run
        weight_decay=0.0,
        dropout=0.0,  # Disabilita il dropout
        lr0=0.01,  # Imposta un learning rate iniziale elevato
        lrf=0.01,  # Imposta un learning rate finale basso
        warmup_epochs=3,  # Numero di epoche di warm-up
        cos_lr=True,  # Abilita la riduzione cosenoide del learning rate
    )
