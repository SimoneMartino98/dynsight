import pathlib
import tkinter as tk


class LabelCreator:
    def __init__(self, master: tk.Tk, image_path: pathlib.Path) -> None:
        self.master = master
        self.master.title("Dynsight: Label Creator")

        # Image Loading
        try:
            self.image = tk.PhotoImage(file=image_path)
        except Exception as e:
            error_message = f"Error loading image: {e}"
            raise ValueError(error_message) from e
            self.master.quit()
            return

        # Main grid
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)  # Image
        self.master.columnconfigure(1, weight=1)  # Sidebar

        # Image canvas
        self.canvas = tk.Canvas(
            self.master,
            width=self.image.width(),
            height=self.image.height(),
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.image)

        # Sidebar canvas
        self.sidebar = tk.Frame(
            self.master,
            width=150,
            padx=10,
            pady=10,
        )
        self.sidebar.grid(row=0, column=1, sticky="ns")
        self.sidebar.grid_propagate(flag=False)

        # Buttons
        self.submit_button = tk.Button(
            self.sidebar,
            text="Submit",
            command=self.submit,
        )
        self.submit_button.pack(pady=10, fill="x")

        self.undo_button = tk.Button(
            self.sidebar,
            text="Undo",
            command=self.undo,
        )
        self.undo_button.pack(pady=10, fill="x")

        self.close_button = tk.Button(
            self.sidebar,
            text="Close",
            command=self.close,
        )
        self.close_button.pack(pady=10, fill="x")

        # Box labelling variables
        self.start_x = None
        self.start_y = None
        self.current_box = None
        self.boxes = []

        # Mouse bindings
        self.canvas.bind("<Button-1>", self.on_click_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_click_release)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)

    def on_click_press(self, event: tk.Event) -> None:
        """Handle mouse click event to start drawing a box."""
        self.start_x = event.x
        self.start_y = event.y
        self.current_box = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="red",
            width=4,
        )

    def on_mouse_drag(self, event: tk.Event) -> None:
        """Handle mouse drag event to update the box size."""
        cur_x, cur_y = event.x, event.y
        self.canvas.coords(
            self.current_box,
            self.start_x,
            self.start_y,
            cur_x,
            cur_y,
        )

    def on_click_release(self, event: tk.Event) -> None:
        """Handle mouse release event to finalize the box."""
        end_x = event.x
        end_y = event.y
        x1, y1 = self.start_x, self.start_y
        x2, y2 = end_x, end_y

        # Box coordinates
        center_x = (x1 + x2) / (2 * self.image.width())
        center_y = (y1 + y2) / (2 * self.image.height())
        width = abs(x2 - x1) / self.image.width()
        height = abs(y2 - y1) / self.image.height()
        box_info = {
            "id": self.current_box,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "center_x": center_x,
            "center_y": center_y,
            "width": width,
            "height": height,
        }
        self.boxes.append(box_info)
        self.current_box = None

    def submit(self) -> None:
        """Submit the labelled boxes and close."""
        if self.boxes:
            self.master.quit()
            return self.boxes
        error_message = "No boxes labelled."
        raise ValueError(error_message)
        self.master.quit()
        return None

    def undo(self) -> None:
        """Undo the last labelled box."""
        if self.boxes:
            last_box = self.boxes.pop()
            self.canvas.delete(last_box["id"])

    def close(self) -> None:
        """Close the label creator."""
        self.master.quit()
