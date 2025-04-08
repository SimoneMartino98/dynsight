import pathlib
import tkinter as tk

from PIL import (
    Image,  # Assicurati di avere installato Pillow (pip install pillow)
)


class LabelCreator:
    def __init__(self, master: tk.Tk, image_path: pathlib.Path) -> None:
        self.master = master
        self.master.title("Dynsight: Label Creator")
        self.image_path = image_path  # percorso originale
        self.masked_image_path = (
            None  # qui salveremo il percorso dell'immagine mascherata
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

        # Pulsante Close per chiudere l'applicazione senza salvare
        self.close_button = tk.Button(
            self.sidebar,
            text="Close",
            command=self.close,
        )
        self.close_button.pack(pady=10, fill="x")

        # Variabili per il labelling
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
        """Finalizza la box al rilascio del pulsante del mouse."""
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
        """Crea un'immagine mascherata copiando solo le parti selezionate su sfondo bianco."""
        if not self.boxes:
            raise ValueError("Nessuna box etichettata.")

        original_image = Image.open(self.image_path)
        output_image = Image.new(
            original_image.mode, original_image.size, color=(255, 255, 255)
        )
        for box in self.boxes:
            x1, y1, x2, y2 = box["abs_coords"]
            cropped = original_image.crop((x1, y1, x2, y2))
            output_image.paste(cropped, (x1, y1))
        # Assicuriamoci che la directory di destinazione esista
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_image.save(output_path)
        print(f"Immagine mascherata salvata in {output_path}")

    def submit(self) -> None:
        """Alla pressione di Submit, salva l'immagine mascherata e chiude la GUI."""
        try:
            # Usa un nome diverso in base all'immagine (es. stem + _masked.jpg)
            masked_name = self.image_path.stem + "_masked.jpg"
            output_path = self.image_path.parent / "cutted" / masked_name
            self.save_selected_boxes(output_path)
            self.masked_image_path = (
                output_path  # memorizza il percorso dell'immagine mascherata
            )
        except Exception as e:
            print(
                f"Errore durante il salvataggio dell'immagine mascherata: {e}"
            )
        finally:
            self.master.quit()

    def get_boxes(self) -> dict:
        """Restituisce il dizionario delle box etichettate."""
        return {box["id"]: box for box in self.boxes}

    def undo(self) -> None:
        """Annulla l'ultima box disegnata."""
        if self.boxes:
            last_box = self.boxes.pop()
            self.canvas.delete(last_box["id"])

    def close(self) -> None:
        """Chiude la GUI senza salvare."""
        self.master.quit()


def label_image(image_path: pathlib.Path) -> (dict, pathlib.Path):
    """Avvia la GUI per etichettare l'immagine e restituisce:
    - Il dizionario delle box etichettate
    - Il percorso dell'immagine mascherata salvata
    """
    root = tk.Tk()
    creator = LabelCreator(root, image_path)
    root.mainloop()
    boxes = creator.get_boxes()
    masked_image = creator.masked_image_path
    root.destroy()
    return boxes, masked_image


def create_dataset(
    train_img_path: pathlib.Path, val_img_path: pathlib.Path
) -> None:
    """Per due immagini (TRAIN e VALIDATION) etichettate separatamente, crea il dataset YOLO:
    - Avvia le sessioni di etichettatura e ottiene le rispettive box e immagini mascherate
    - Crea le cartelle (images/train, images/val, labels/train, labels/val)
    - Copia le immagini mascherate nelle rispettive cartelle
    - Scrive i file delle etichette e il file YAML
    """
    print("Etichettare l'immagine di TRAIN:")
    train_boxes, train_masked = label_image(train_img_path)
    print("Etichettare l'immagine di VALIDATION:")
    val_boxes, val_masked = label_image(val_img_path)

    # Percorsi di base per il dataset
    dataset_base = pathlib.Path("dataset_guess")
    yaml_file = dataset_base / "dataset_guess.yaml"
    img_train = dataset_base / "images" / "train"
    img_val = dataset_base / "images" / "val"
    lab_train = dataset_base / "labels" / "train"
    lab_val = dataset_base / "labels" / "val"

    # Creazione delle directory
    for path in [img_train, img_val, lab_train, lab_val]:
        path.mkdir(parents=True, exist_ok=True)

    # Copia delle immagini mascherate (non quelle originali)
    if train_masked is not None and train_masked.is_file():
        destination_train = img_train / train_masked.name
        destination_train.write_bytes(train_masked.read_bytes())
    else:
        print("Avviso: Immagine mascherata TRAIN non trovata.")

    if val_masked is not None and val_masked.is_file():
        destination_val = img_val / val_masked.name
        destination_val.write_bytes(val_masked.read_bytes())
    else:
        print("Avviso: Immagine mascherata VALIDATION non trovata.")

    # Scrittura dei file di etichette
    output_file_train = lab_train / "0.txt"
    with output_file_train.open("w") as f:
        for _, box in train_boxes.items():
            f.write(
                f"0 {box['center_x']:.6f} {box['center_y']:.6f} "
                f"{box['width']:.6f} {box['height']:.6f}\n"
            )

    output_file_val = lab_val / "0.txt"
    with output_file_val.open("w") as f:
        for _, box in val_boxes.items():
            f.write(
                f"0 {box['center_x']:.6f} {box['center_y']:.6f} "
                f"{box['width']:.6f} {box['height']:.6f}\n"
            )

    # Creazione del file YAML
    with yaml_file.open("w") as f:
        f.write(f"train: {img_train!s}\n")
        f.write(f"val: {img_val!s}\n")
        f.write("nc: 1\n")
        f.write("names: ['object']\n")
    print("Dataset e file YAML creati correttamente.")
