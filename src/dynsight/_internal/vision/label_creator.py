import pathlib
import tkinter as tk

from PIL import (
    Image,  # Assicurati di avere installato Pillow (pip install pillow)
)


class LabelCreator:
    def __init__(self, master: tk.Tk, image_path: pathlib.Path) -> None:
        self.master = master
        self.master.title("Dynsight: Label Creator")
        self.image_path = (
            image_path  # Salviamo il percorso per poter riaprire l'immagine
        )

        # Caricamento immagine
        try:
            self.image = tk.PhotoImage(file=image_path)
        except Exception as e:
            error_message = f"Error loading image: {e}"
            raise ValueError(error_message) from e
            self.master.quit()
            return

        # Griglia principale
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)  # Immagine
        self.master.columnconfigure(1, weight=1)  # Sidebar

        # Canvas per l'immagine
        self.canvas = tk.Canvas(
            self.master,
            width=self.image.width(),
            height=self.image.height(),
            cursor="crosshair",
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.image)
        # Linee orizzontali e verticali
        self.h_line = self.canvas.create_line(
            0, 0, self.image.width(), 0, fill="blue", dash=(2, 2), width=3
        )
        self.v_line = self.canvas.create_line(
            0, 0, 0, self.image.height(), fill="blue", dash=(2, 2), width=3
        )

        # Sidebar
        self.sidebar = tk.Frame(
            self.master,
            width=150,
            padx=10,
            pady=10,
        )
        self.sidebar.grid(row=0, column=1, sticky="ns")
        self.sidebar.grid_propagate(flag=False)

        # Pulsanti
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

        # Variabili per il labelling delle box
        self.start_x = None
        self.start_y = None
        self.current_box = None
        self.boxes = []  # Ogni box è un dizionario che contiene le coordinate relative e assolute

        # Binding del mouse
        self.canvas.bind("<Button-1>", self.on_click_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_click_release)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<Motion>", self.follow_mouse)

    def follow_mouse(self, event: tk.Event) -> None:
        """Aggiorna le linee orizzontali e verticali per seguire il mouse."""
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
        """Aggiorna le dimensioni della box durante il trascinamento del mouse."""
        cur_x, cur_y = event.x, event.y
        self.canvas.coords(
            self.current_box,
            self.start_x,
            self.start_y,
            cur_x,
            cur_y,
        )
        x, y = event.x, event.y
        self.canvas.coords(self.h_line, 0, y, self.image.width(), y)
        self.canvas.coords(self.v_line, x, 0, x, self.image.height())

    def on_click_release(self, event: tk.Event) -> None:
        """Finalizza la box al rilascio del pulsante del mouse."""
        end_x = event.x
        end_y = event.y
        # Calcola le coordinate assolute della box (minimi e massimi)
        x1, y1 = self.start_x, self.start_y
        x2, y2 = end_x, end_y
        abs_coords = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        # Coordinate relative (normalizzate rispetto all'immagine)
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
            "abs_coords": abs_coords,  # Coordinate per il ritaglio
        }
        self.boxes.append(box_info)
        self.current_box = None

    def save_selected_boxes(self, output_path: pathlib.Path) -> None:
        """Salva una nuova immagine con le stesse dimensioni dell'immagine originale.
        In questa immagine solo il contenuto delle box selezionate viene copiato
        nelle stesse posizioni, mentre il resto viene riempito (ad es. di bianco).
        """
        if not self.boxes:
            raise ValueError("Nessuna box etichettata.")

        original_image = Image.open(self.image_path)
        # Crea una nuova immagine con lo stesso formato e dimensioni dell'originale.
        output_image = Image.new(
            original_image.mode, original_image.size, color=(255, 255, 255)
        )

        # Per ogni box, ritaglia l'area dall'immagine originale e incollala nella stessa posizione.
        for box in self.boxes:
            x1, y1, x2, y2 = box["abs_coords"]
            cropped = original_image.crop((x1, y1, x2, y2))
            output_image.paste(cropped, (x1, y1))

        output_image.save(output_path)
        print(f"Immagine salvata in {output_path}")

    def submit(self) -> None:
        """Quando viene premuto il pulsante Submit, salva l'immagine che contiene
        solo il contenuto delle box selezionate (nelle stesse posizioni) e chiude l'applicazione.
        """
        try:
            # Specifica il percorso di output dove salvare l'immagine
            output_path = self.image_path.parent / "cutted/0.jpg"
            self.save_selected_boxes(output_path)
        except Exception as e:
            print(f"Errore durante il salvataggio: {e}")
        finally:
            self.master.quit()

    def get_boxes(self) -> dict:
        return {box["id"]: box for box in self.boxes}

    def undo(self) -> None:
        """Annulla l'ultima box etichettata."""
        if self.boxes:
            last_box = self.boxes.pop()
            self.canvas.delete(last_box["id"])

    def close(self) -> None:
        """Chiude il Label Creator."""
        self.master.quit()
