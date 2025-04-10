from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib

import os
import shutil
import tkinter as tk

import cv2
from PIL import Image
from ultralytics import YOLO

from .vision_gui import CreateGuessDataset


def _numerical_sort(filename):
    return int(os.path.splitext(filename)[0])


def _merge_folders(src_folder, dest_folder):
    for root, dirs, files in os.walk(src_folder):
        relative_path = os.path.relpath(root, src_folder)
        dest_path = os.path.join(dest_folder, relative_path)

        if not os.path.exists(dest_path):
            os.makedirs(dest_path)

        for file in files:
            src_file = os.path.join(root, file)
            dest_file = os.path.join(dest_path, file)

            shutil.move(src_file, dest_file)


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


def create_guess_dataset(
    img_path: pathlib.Path,
) -> None:
    root = tk.Tk()
    CreateGuessDataset(root, img_path)
    root.mainloop()


def train_model_from_guess_dataset(
    frames_folder: pathlib.Path,
    yaml_file: pathlib.Path,
    starting_model: str | pathlib.Path = "yolo12x.pt",
) -> None:
    # Defining starting point
    model = YOLO(starting_model)
    model.train(
        data=yaml_file,  # Path to the dataset configuration file
        epochs=2,  # Number of training epochs
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
    print("ok")
    model = YOLO("runs/detect/pretrained_model/weights/best.pt")
    for frame_file in sorted(os.listdir(frames_folder), key=_numerical_sort):
        with Image.open(f"{frames_folder}/{frame_file}") as img:
            width, height = img.size
        model.predict(
            source=f"{frames_folder}/{frame_file}",
            imgsz=(height, width),
            augment=True,
            save=True,
            save_txt=True,
            save_conf=True,
            show_labels=False,
            name="prediction_name",
            iou=0.1,
            max_det=200000,
            project="prediction_project",
            line_width=2,
            agnostic_nms=True,
        )
    print("ok2")
    prediction_path = "prediction_project"
    prediction_name = "prediction_name"
    main_folder = pathlib.Path(prediction_path) / prediction_name
    for folder_name in os.listdir(prediction_path):
        if (
            folder_name.startswith(prediction_name)
            and folder_name != prediction_name
        ):
            folder_path = pathlib.Path(prediction_path) / folder_name
            if folder_path.is_dir():
                _merge_folders(folder_path, main_folder)
                shutil.rmtree(folder_path)
    print("ok3")
