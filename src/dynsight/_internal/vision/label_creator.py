import pathlib
import tkinter as tk

from PIL import Image


class LabelCreator:
    def __init__(
        self,
        master: tk.Tk,
        image_path: pathlib.Path,
        target_output: pathlib.Path = None,
    ) -> None:
        self.master = master
        self.master.title("Dynsight: Label Creator")
        self.image_path = image_path  # Original image path
        self.target_output = (
            target_output  # Path to save the masked image (if provided)
        )
        self.masked_image_path = (
            None  # Will be set when saving the masked image
        )

        # Load image
        try:
            self.image = tk.PhotoImage(file=image_path)
        except Exception as e:
            error_message = f"Error loading image: {e}"
            raise ValueError(error_message) from e
            self.master.quit()
            return

        # Main grid configuration
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)  # Image column
        self.master.columnconfigure(1, weight=1)  # Sidebar column

        # Canvas for the image
        self.canvas = tk.Canvas(
            self.master,
            width=self.image.width(),
            height=self.image.height(),
            cursor="crosshair",
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.image)

        # Guidelines for the cursor
        self.h_line = self.canvas.create_line(
            0, 0, self.image.width(), 0, fill="blue", dash=(2, 2), width=3
        )
        self.v_line = self.canvas.create_line(
            0, 0, 0, self.image.height(), fill="blue", dash=(2, 2), width=3
        )

        # Sidebar for buttons
        self.sidebar = tk.Frame(self.master, width=150, padx=10, pady=10)
        self.sidebar.grid(row=0, column=1, sticky="ns")
        self.sidebar.grid_propagate(False)

        # Buttons
        self.submit_button = tk.Button(
            self.sidebar, text="Submit", command=self.submit
        )
        self.submit_button.pack(pady=10, fill="x")
        self.undo_button = tk.Button(
            self.sidebar, text="Undo", command=self.undo
        )
        self.undo_button.pack(pady=10, fill="x")
        self.close_button = tk.Button(
            self.sidebar, text="Close", command=self.close
        )
        self.close_button.pack(pady=10, fill="x")

        # Variables for label management
        self.start_x = None
        self.start_y = None
        self.current_box = None
        self.boxes = []  # Each box is a dict with relative and absolute coordinates

        # Mouse binding
        self.canvas.bind("<Button-1>", self.on_click_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_click_release)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<Motion>", self.follow_mouse)

    def follow_mouse(self, event: tk.Event) -> None:
        """Update guidelines to follow the cursor."""
        x, y = event.x, event.y
        self.canvas.coords(self.h_line, 0, y, self.image.width(), y)
        self.canvas.coords(self.v_line, x, 0, x, self.image.height())

    def on_click_press(self, event: tk.Event) -> None:
        """Start drawing a box on mouse click."""
        self.start_x = event.x
        self.start_y = event.y
        self.current_box = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="red",
            width=3,
        )

    def on_mouse_drag(self, event: tk.Event) -> None:
        """Update the box size as the mouse drags."""
        cur_x, cur_y = event.x, event.y
        self.canvas.coords(
            self.current_box, self.start_x, self.start_y, cur_x, cur_y
        )
        self.canvas.coords(
            self.h_line, 0, event.y, self.image.width(), event.y
        )
        self.canvas.coords(
            self.v_line, event.x, 0, event.x, self.image.height()
        )

    def on_click_release(self, event: tk.Event) -> None:
        """Finalize the box on mouse release."""
        end_x = event.x
        end_y = event.y
        x1, y1 = self.start_x, self.start_y
        x2, y2 = end_x, end_y
        abs_coords = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        center_x = (x1 + x2) / (2 * self.image.width())
        center_y = (y1 + y2) / (2 * self.image.height())
        width_rel = abs(x2 - x1) / self.image.width()
        height_rel = abs(y2 - y1) / self.image.height()
        box_info = {
            "id": self.current_box,
            "center_x": center_x,
            "center_y": center_y,
            "width": width_rel,
            "height": height_rel,
            "abs_coords": abs_coords,
        }
        self.boxes.append(box_info)
        self.current_box = None

    def save_selected_boxes(self, output_path: pathlib.Path) -> None:
        """Create and save the masked image, copying only the selected parts on a white background."""
        if not self.boxes:
            raise ValueError("No boxes labeled.")

        original_image = Image.open(self.image_path)
        output_image = Image.new(
            original_image.mode, original_image.size, color=(255, 255, 255)
        )

        for box in self.boxes:
            x1, y1, x2, y2 = box["abs_coords"]
            cropped = original_image.crop((x1, y1, x2, y2))
            output_image.paste(cropped, (x1, y1))

        # Ensure the destination directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_image.save(output_path)
        print(f"Masked image saved at {output_path}")

    def submit(self) -> None:
        """Save the masked image and close the GUI."""
        try:
            if self.target_output is None:
                # Default behavior: use the 'cutted' folder
                masked_name = self.image_path.stem + ".jpg"
                output_path = self.image_path.parent / "cutted" / masked_name
            else:
                output_path = self.target_output
            self.save_selected_boxes(output_path)
            self.masked_image_path = output_path  # Save the path
        except Exception as e:
            print(f"Error saving masked image: {e}")
        finally:
            self.master.quit()

    def get_boxes(self) -> dict:
        """Return the labeled boxes dictionary."""
        return {box["id"]: box for box in self.boxes}

    def undo(self) -> None:
        """Undo the last drawn box."""
        if self.boxes:
            last_box = self.boxes.pop()
            self.canvas.delete(last_box["id"])

    def close(self) -> None:
        """Close the GUI without saving."""
        self.master.quit()


def label_image(
    image_path: pathlib.Path, target_output: pathlib.Path = None
) -> (dict, pathlib.Path):
    """Launch the GUI for labeling the image and return:
    - The dictionary of labeled boxes
    - The path of the saved masked image
    """
    root = tk.Tk()
    creator = LabelCreator(root, image_path, target_output)
    root.mainloop()
    boxes = creator.get_boxes()
    masked_image = creator.masked_image_path
    root.destroy()
    return boxes, masked_image


def subdivide_image(image_path: pathlib.Path, box_size: int = 256) -> None:
    """Split the image into smaller square patches and adjust the labels accordingly."""
    original_image = Image.open(image_path)
    width, height = original_image.size

    patches = []
    boxes = []  # Holds box info for each patch
    for i in range(0, width, box_size):
        for j in range(0, height, box_size):
            box = (i, j, min(i + box_size, width), min(j + box_size, height))
            patch = original_image.crop(box)
            patches.append(patch)

            # Adjust the boxes' coordinates relative to the patch
            patch_boxes = []
            for box_info in boxes:
                x1, y1, x2, y2 = box_info["abs_coords"]
                if (
                    x1 < i + box_size
                    and y1 < j + box_size
                    and x2 > i
                    and y2 > j
                ):
                    adjusted_box = {
                        "center_x": (x1 + x2) / (2 * box_size),
                        "center_y": (y1 + y2) / (2 * box_size),
                        "width": (x2 - x1) / box_size,
                        "height": (y2 - y1) / box_size,
                        "abs_coords": (
                            max(x1, i),
                            max(y1, j),
                            min(x2, i + box_size),
                            min(y2, j + box_size),
                        ),
                    }
                    patch_boxes.append(adjusted_box)
            boxes.append(patch_boxes)

    return patches, boxes


def create_dataset(
    train_img_path: pathlib.Path, val_img_path: pathlib.Path
) -> None:
    """Create dataset from subdivided images, including labels, masks, and YAML file."""
    dataset_base = pathlib.Path("dataset_guess")
    img_train_folder = dataset_base / "images" / "train"
    img_val_folder = dataset_base / "images" / "val"

    # Split the train and validation images into smaller patches
    print("Subdividing train image:")
    train_patches, train_boxes = subdivide_image(train_img_path)
    print("Subdividing validation image:")
    val_patches, val_boxes = subdivide_image(val_img_path)

    # Save patches and create label files
    for i, (train_patch, boxes) in enumerate(zip(train_patches, train_boxes)):
        train_patch_path = img_train_folder / f"train_{i}.jpg"
        train_patch.save(train_patch_path)
        with (dataset_base / "labels" / "train" / f"{i}.txt").open("w") as f:
            for box in boxes:
                f.write(
                    f"0 {box['center_x']:.6f} {box['center_y']:.6f} {box['width']:.6f} {box['height']:.6f}\n"
                )

    for i, (val_patch, boxes) in enumerate(zip(val_patches, val_boxes)):
        val_patch_path = img_val_folder / f"val_{i}.jpg"
        val_patch.save(val_patch_path)
        with (dataset_base / "labels" / "val" / f"{i}.txt").open("w") as f:
            for box in boxes:
                f.write(
                    f"0 {box['center_x']:.6f} {box['center_y']:.6f} {box['width']:.6f} {box['height']:.6f}\n"
                )

    # Create the YAML file for YOLO training configuration
    yaml_file = pathlib.Path("dataset_guess.yaml")
    with yaml_file.open("w") as f:
        f.write(f"train: {img_train_folder!s}\n")
        f.write(f"val: {img_val_folder!s}\n")
        f.write("nc: 1\n")
        f.write("names: ['object']\n")

    print("Dataset and YAML file created successfully.")
