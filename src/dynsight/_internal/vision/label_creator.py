import pathlib
import tkinter as tk
from pathlib import Path

from PIL import Image


class LabelCreator:
    def __init__(
        self,
        master: tk.Tk,
        image_path: pathlib.Path,
    ) -> None:
        self.master = master
        self.master.title("Dynsight: Label Creator")
        self.image_path = image_path  # percorso originale
        self.masked_image_path = (
            None  # verrà impostato al salvataggio dell'immagine mascherata
        )

        # Caricamento immagine
        try:
            self.image = tk.PhotoImage(file=image_path)
        except Exception as e:
            error_message = f"Error loading image: {e}"
            raise ValueError(error_message) from e
            self.master.quit()
            return

        # Configurazione della griglia principale
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)  # Colonna per l'immagine
        self.master.columnconfigure(1, weight=1)  # Colonna per la sidebar

        # Canvas per l'immagine
        self.canvas = tk.Canvas(
            self.master,
            width=self.image.width(),
            height=self.image.height(),
            cursor="crosshair",
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.image)
        # Linee guida per il cursore
        self.h_line = self.canvas.create_line(
            0, 0, self.image.width(), 0, fill="blue", dash=(2, 2), width=3
        )
        self.v_line = self.canvas.create_line(
            0, 0, 0, self.image.height(), fill="blue", dash=(2, 2), width=3
        )

        # Sidebar per i pulsanti
        self.sidebar = tk.Frame(
            self.master,
            width=150,
            padx=10,
            pady=10,
        )
        self.sidebar.grid(row=0, column=1, sticky="ns")
        self.sidebar.grid_propagate(False)

        # Pulsante Submit: salva l'immagine mascherata e chiude la GUI
        self.submit_button = tk.Button(
            self.sidebar,
            text="Submit",
            command=self.submit,
        )
        self.submit_button.pack(pady=10, fill="x")

        # Pulsante Undo per eliminare l'ultima box etichettata
        self.undo_button = tk.Button(
            self.sidebar,
            text="Undo",
            command=self.undo,
        )
        self.undo_button.pack(pady=10, fill="x")

        # Pulsante Close per chiudere senza salvare
        self.close_button = tk.Button(
            self.sidebar,
            text="Close",
            command=self.close,
        )
        self.close_button.pack(pady=10, fill="x")

        # Variabili per la gestione dell'etichettatura
        self.start_x = None
        self.start_y = None
        self.current_box = None
        self.boxes = []  # ogni box è un dizionario con coordinate relative e assolute

        # Binding del mouse
        self.canvas.bind("<Button-1>", self.on_click_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_click_release)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<Motion>", self.follow_mouse)

    def follow_mouse(self, event: tk.Event) -> None:
        """Aggiorna le linee guida per seguire il cursore."""
        x, y = event.x, event.y
        self.canvas.coords(self.h_line, 0, y, self.image.width(), y)
        self.canvas.coords(self.v_line, x, 0, x, self.image.height())

    def on_click_press(self, event: tk.Event) -> None:
        """Inizia a disegnare una box al click del mouse."""
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
        """Aggiorna la dimensione della box mentre si trascina il mouse."""
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
        """Finalizza la box al rilascio del pulsante del mouse e salva la porzione di immagine."""
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

    def create_collage(self) -> None:
        """Crea un collage casuale con le immagini ritagliate."""
        collage_size = (1080, 1080)
        collage = Image.new("RGB", collage_size, "white")
        used_positions = []

        cropped_dir = Path("cropped_selection")
        if not cropped_dir.exists():
            print("No cropped images found.")
            return

        cropped_images = list(cropped_dir.glob("*.png"))
        if not cropped_images:
            print("No cropped images found.")
            return

        for cropped_image_path in cropped_images:
            cropped_image = Image.open(cropped_image_path)
            cropped_image.thumbnail(
                (200, 200)
            )  # Resize to fit within the collage

            max_attempts = 100
            for _ in range(max_attempts):
                x = random.randint(0, collage_size[0] - cropped_image.width)
                y = random.randint(0, collage_size[1] - cropped_image.height)
                new_box = (
                    x,
                    y,
                    x + cropped_image.width,
                    y + cropped_image.height,
                )

                # Check for overlap
                if all(
                    not (
                        new_box[0] < pos[2]
                        and new_box[2] > pos[0]
                        and new_box[1] < pos[3]
                        and new_box[3] > pos[1]
                    )
                    for pos in used_positions
                ):
                    used_positions.append(new_box)
                    collage.paste(cropped_image, (x, y))
                    break
            else:
                print(
                    f"Could not place image {cropped_image_path} without overlap."
                )

        collage.save("collage.png")
        print("Collage saved as collage.png")

    def submit(self) -> None:
        # Salva tutte le porzioni di immagine al submit
        pil_image = Image.open(self.image_path)
        for i, box in enumerate(self.boxes):
            abs_coords = box["abs_coords"]
            cropped_image = pil_image.crop(abs_coords)
            save_path = Path(f"cropped_selection/{i + 1}.png")
            save_path.parent.mkdir(parents=True, exist_ok=True)
            cropped_image.save(save_path)
            print(f"Saved cropped image to {save_path}")
        create_collage()
        self.master.quit()

    def undo(self) -> None:
        """Annulla l'ultima box disegnata."""
        if self.boxes:
            last_box = self.boxes.pop()
            self.canvas.delete(last_box["id"])

    def close(self) -> None:
        """Chiude la GUI senza salvare."""
        self.master.quit()
