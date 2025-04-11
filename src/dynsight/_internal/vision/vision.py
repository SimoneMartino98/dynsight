from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib

import os
import re
import shutil
import tkinter as tk
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit
from scipy.stats import norm
from ultralytics import YOLO

from .vision_gui import CreateGuessDataset


def _gaussian(x: float, mu: float, sigma: float, amplitude: float) -> float:
    return amplitude * norm.pdf(x, mu, sigma)


def _extract_number(file_name) -> int:
    match = re.search(r"\d+", file_name)
    return int(match.group()) if match else 0


def _numerical_sort(filename) -> int:
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


def _prediction_reader(
    labels_path,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    file_names = [f for f in os.listdir(labels_path) if f.endswith(".txt")]
    file_names.sort(key=_extract_number)

    detections = []
    all_frame_types = []
    all_frame_x = []
    all_frame_y = []
    all_frame_xl = []
    all_frame_yl = []
    all_frame_acc = []

    for file_name in file_names:
        file_path = os.path.join(labels_path, file_name)

        frame_type = []
        frame_x = []
        frame_y = []
        frame_xl = []
        frame_yl = []
        frame_acc = []

        with open(file_path) as file:
            for line in file:
                types, x, y, x_l, y_l, acc = line.strip().split()
                frame_type.append(int(types))
                frame_x.append(float(x))
                frame_y.append(float(y))
                frame_xl.append(float(x_l))
                frame_yl.append(float(y_l))
                frame_acc.append(float(acc))

        detections.append(len(frame_type))
        all_frame_types.append(np.array(frame_type))
        all_frame_x.append(np.array(frame_x))
        all_frame_y.append(np.array(frame_y))
        all_frame_xl.append(np.array(frame_xl))
        all_frame_yl.append(np.array(frame_yl))
        all_frame_acc.append(np.array(frame_acc))

    detections = np.array(detections)
    all_frame_types = np.array(all_frame_types, dtype=object)
    all_frame_x = np.array(all_frame_x, dtype=object)
    all_frame_y = np.array(all_frame_y, dtype=object)
    all_frame_xl = np.array(all_frame_xl, dtype=object)
    all_frame_yl = np.array(all_frame_yl, dtype=object)
    all_frame_acc = np.array(all_frame_acc, dtype=object)

    return (
        detections,
        all_frame_types,
        all_frame_x,
        all_frame_y,
        all_frame_xl,
        all_frame_yl,
        all_frame_acc,
    )


def _gaussian_outliers(
    distribution, thr: float = 1e-5, plot_name: str = "gaussian_outliers"
):
    # Plot histogram and use normalized data for fitting
    hist, bin_edges = np.histogram(distribution, bins="auto", density=True)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    # Fit using curve_fit
    popt, pcov = curve_fit(
        _gaussian,
        bin_centers,
        hist,
        p0=[np.mean(distribution), np.std(distribution), max(hist)],
    )

    # Extract fitted parameters
    mu, sigma, amplitude = popt

    # Generate Gaussian curve for plotting
    x = np.linspace(bin_edges[0], bin_edges[-1], 1000)
    fitted_curve = _gaussian(x, mu, sigma, amplitude)

    # Calculate thresholds
    threshold_pdf = thr  # PDF value close to zero
    x_threshold_min = mu - np.sqrt(
        -2
        * sigma**2
        * np.log(threshold_pdf / (amplitude / (sigma * np.sqrt(2 * np.pi))))
    )
    x_threshold_max = mu + np.sqrt(
        -2
        * sigma**2
        * np.log(threshold_pdf / (amplitude / (sigma * np.sqrt(2 * np.pi))))
    )

    # Identify outliers
    outlier = [
        value
        for value in distribution
        if value < x_threshold_min or value > x_threshold_max
    ]
    # Plot histogram and fitted curve
    plt.hist(
        distribution,
        bins="auto",
        density=True,
        alpha=0.6,
        color="g",
        label="Histogram",
    )
    plt.plot(
        x,
        fitted_curve,
        "k",
        linewidth=2,
        label=f"Improved Gaussian fit\n$\\mu={mu:.2f}, \\sigma={sigma:.2f}$",
    )

    # Add vertical lines for thresholds
    plt.axvline(
        x_threshold_min,
        color="r",
        linestyle="--",
        label=f"Threshold Min = {x_threshold_min:.2f}",
    )
    plt.axvline(
        x_threshold_max,
        color="b",
        linestyle="--",
        label=f"Threshold Max = {x_threshold_max:.2f}",
    )

    plt.legend(loc="best")
    plt.title("Histogram with Gaussian Fit and Thresholds")
    plt.xlabel("Values")
    plt.ylabel("Density")
    plt.tight_layout()
    plt.savefig(f"{plot_name}.png")
    # Return the outlier list
    return outlier


def train_model_from_guess_dataset(
    frames_folder: pathlib.Path,
    yaml_file: pathlib.Path,
    results_folder: pathlib.Path,  # Path to save the results
    starting_model: str | pathlib.Path = "yolo12x.pt",
) -> None:
    # Defining starting point
    model = YOLO(starting_model)
    # model.train(
    #     data=yaml_file,  # Path to the dataset configuration file
    #     epochs=2,  # Number of training epochs
    #     imgsz=1080,  # Image size
    #     batch=2,  # Batch size (adjust based on GPU memory)
    #     lr0=0.001,  # Initial learning rate
    #     lrf=0.01,  # Final learning rate (scheduler)
    #     optimizer="auto",  # Use Stochastic Gradient Descent (try 'Adam' too)
    #     augment=True,  # Enable augmentations
    #     fliplr=0.5,  # Horizontal flip probability
    #     flipud=0.5,  # Vertical flip probability
    #     hsv_h=0.015,  # Adjust hue
    #     hsv_s=0.7,  # Adjust saturation
    #     hsv_v=0.4,  # Adjust brightness
    #     mosaic=0.5,  # Enable mosaic augmentation
    #     mixup=0.0,  # MixUp augmentation
    #     device=[2, 3],  # Use multiple GPUs
    #     patience=10,  # Early stopping patience
    #     workers=16,  # Number of workers for data loading
    #     name="pretrained_model",  # Name for the training run
    # )
    print("okkkk")
    model = YOLO("runs/detect/pretrained_model/weights/best.pt")
    # for frame_file in sorted(os.listdir(frames_folder), key=_numerical_sort):
    #     with Image.open(f"{frames_folder}/{frame_file}") as img:
    #         width, height = img.size
    #     model.predict(
    #         source=f"{frames_folder}/{frame_file}",
    #         imgsz=(height, width),
    #         augment=True,
    #         save=True,
    #         save_txt=True,
    #         save_conf=True,
    #         show_labels=False,
    #         name="prediction_name",
    #         iou=0.1,
    #         max_det=200000,
    #         project="prediction_project",
    #         line_width=2,
    #         agnostic_nms=True,
    #     )
    print("ok2")
    prediction_path = "prediction_project"
    prediction_name = "prediction_name"
    main_folder = os.path.join(prediction_path, prediction_name)
    for folder_name in os.listdir(prediction_path):
        if (
            folder_name.startswith(prediction_name)
            and folder_name != prediction_name
        ):
            folder_path = os.path.join(prediction_path, folder_name)
            if os.path.isdir(folder_path):
                print(f"Merging {folder_path} in {main_folder}...")
                _merge_folders(folder_path, main_folder)
                shutil.rmtree(folder_path)
    print("Merge complete")
    print("ok3")
    detections, types, frame_x, frame_y, frame_xl, frame_yl, frame_acc = (
        _prediction_reader(Path(f"{prediction_path}/{prediction_name}/labels"))
    )
    n_frames = detections.shape[0]
    outliers_xl = np.array(
        _gaussian_outliers(
            np.hstack(frame_xl), thr=0.55, plot_name="outliers_xl"
        )
    )
    outliers_yl = np.array(
        _gaussian_outliers(
            np.hstack(frame_yl), thr=0.55, plot_name="outliers_yl"
        )
    )
    for i in range(n_frames):
        current_frame_detections = detections[i]
        current_frame_acc = frame_acc[i]
        current_frame_xl = frame_xl[i]
        current_frame_yl = frame_yl[i]
        current_frame_x = frame_x[i]
        current_frame_y = frame_y[i]
        current_types = types[i]

        mask = (
            np.isin(current_frame_xl, outliers_xl)
            | np.isin(current_frame_yl, outliers_yl)
            | (current_frame_acc <= 0.30)
        )
        results_folder.mkdir(parents=True, exist_ok=True)
        with open(results_folder / f"{i}.txt", "w") as file:
            for j in range(current_frame_detections):
                if mask[j]:
                    print(
                        f"DELETED Frame: {i}-{j} - {current_types[j]} {current_frame_x[j]} {current_frame_y[j]} {current_frame_xl[j]} {current_frame_yl[j]} {current_frame_acc[j]}"
                    )
                else:
                    file.write(
                        f"{current_types[j]} {current_frame_x[j]} {current_frame_y[j]} {current_frame_xl[j]} {current_frame_yl[j]}\n"
                    )
