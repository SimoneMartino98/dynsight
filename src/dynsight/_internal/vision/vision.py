from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib
from pathlib import Path

import cv2
from ultralytics import YOLO

from .label_creator import LabelCreator


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


def train_model(img_path: pathlib.Path) -> None:
    """Train a model using the provided image path."""
    root = tk.Tk()
    app = LabelCreator(root, img_path)
    root.mainloop()
    res = app.get_boxes()

    # Creating the guess dataset
    guess_dataset_lab_path = Path("dataset_guess/labels/train")
    guess_dataset_img_path = Path("dataset_guess/images/train")
    guess_dataset_img_path.mkdir(parents=True, exist_ok=True)
    if img_path.is_file():
        destination = guess_dataset_img_path / img_path.name
        destination.write_bytes(img_path.read_bytes())
    guess_dataset_lab_path.mkdir(parents=True, exist_ok=True)
    output_file = guess_dataset_lab_path / "0.txt"
    with output_file.open("w") as f:
        for vals in res.values():
            f.write(
                f"0 {vals['center_x']:.6f} {vals['center_y']:.6f} {vals['width']:.6f} {vals['height']:.6f}\n"
            )
    # Creating the .yaml file for training
    yaml_file = Path("training_option.yaml", exist_ok=True)
    with Path.open(yaml_file, "w") as f:
        f.write(
            f"train: {guess_dataset_img_path!s}\n"
            f"val: {guess_dataset_img_path!s}\n"
            f"nc: 1\n"
            f"names: ['object']\n"
        )
    # Initial training
    model = YOLO("yolo12x.pt")
    model.train(
        data=yaml_file,  # Path to the dataset configuration file
        epochs=100,  # Number of training epochs
        imgsz=1080,  # Image size
        batch=2,  # Batch size (adjust based on GPU memory)
        lr0=0.001,  # Initial learning rate
        lrf=0.01,  # Final learning rate (scheduler)
        optimizer="auto",  # Use Stochastic Gradient Descent (try 'Adam' too)
        augment=True,  # Enable augmentations
        fliplr=0.5,  # Horizontal flip probability
        flipud=0.5,  # Vertical flip probability
        hsv_h=0.015,  # Adjust hue
        hsv_s=0.7,  # Adjust saturation
        hsv_v=0.4,  # Adjust brightness
        mosaic=0.5,  # Enable mosaic augmentation
        mixup=0.0,  # MixUp augmentation
        device=[2, 3],  # Use multiple GPUs
        patience=10,  # Early stopping patience
        workers=16,  # Number of workers for data loading
        name="pretrained_model",  # Name for the training run
    )
