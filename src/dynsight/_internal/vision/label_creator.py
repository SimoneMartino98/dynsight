import pathlib
import tkinter as tk

import albumentations as A
import cv2
import numpy as np
from PIL import Image


# Modifica della classe LabelCreator
class LabelCreator:
    def __init__(
        self,
        master: tk.Tk,
        image_path: pathlib.Path,
        target_output: pathlib.Path = None,
        augmentations: int = 5,  # numero di duplicati che vuoi generare
    ) -> None:
        self.master = master
        self.master.title("Dynsight: Label Creator")
        self.image_path = image_path  # percorso originale
        self.target_output = target_output  # percorso di destinazione per l'immagine mascherata (se fornito)
        self.masked_image_path = (
            None  # verrà impostato al salvataggio dell'immagine mascherata
        )
        self.augmentations = augmentations

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

        # Augmentation pipeline
        self.transform = A.Compose(
            [
                A.HorizontalFlip(p=0.5),
                A.RandomBrightnessContrast(p=0.5),
                A.ShiftScaleRotate(
                    shift_limit=0.05, scale_limit=0.1, rotate_limit=15, p=0.5
                ),
                A.GaussNoise(var_limit=(10.0, 50.0), p=0.3),
                A.HueSaturationValue(
                    hue_shift_limit=10,
                    sat_shift_limit=15,
                    val_shift_limit=10,
                    p=0.3,
                ),
                A.MotionBlur(p=0.2),
            ]
        )

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
        """Crea e salva l'immagine mascherata, copiando solo le parti selezionate su sfondo bianco."""
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

    def augment_and_save(
        self, image: np.array, boxes: list, output_folder: pathlib.Path
    ) -> None:
        """Genera versioni augmentate dell'immagine e salva insieme alle annotazioni YOLO."""
        for i in range(self.augmentations):
            augmented = self.transform(image=image)
            aug_image = augmented["image"]
            aug_boxes = augmented["bboxes"]

            # Salva immagine
            aug_image_path = (
                output_folder / f"{self.image_path.stem}_aug_{i:02d}.jpg"
            )
            cv2.imwrite(
                str(aug_image_path), cv2.cvtColor(aug_image, cv2.COLOR_RGB2BGR)
            )
            print(f"Salvata: {aug_image_path}")

            # Salva file di annotazione YOLO
            aug_txt_path = (
                output_folder / f"{self.image_path.stem}_aug_{i:02d}.txt"
            )
            with open(aug_txt_path, "w") as f:
                for box in aug_boxes:
                    center_x, center_y, width, height = box
                    f.write(f"0 {center_x} {center_y} {width} {height}\n")
            print(f"Salvato: {aug_txt_path}")

    def submit(self) -> None:
        """Alla pressione di Submit, salva l'immagine mascherata e chiude la GUI."""
        try:
            if self.target_output is None:
                # Comportamento di default: usa la cartella 'cutted'
                masked_name = self.image_path.stem + "_masked.jpg"
                output_path = self.image_path.parent / "cutted" / masked_name
            else:
                output_path = self.target_output

            self.save_selected_boxes(output_path)
            self.masked_image_path = output_path  # salva il percorso
            # Augmenta e salva le versioni
            self.augment_and_save(
                np.array(self.image), self.boxes, self.target_output
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


def label_image(
    image_path: pathlib.Path,
    target_output: pathlib.Path = None,
    augmentations: int = 5,
) -> (dict, pathlib.Path):
    """Avvia la GUI per etichettare l'immagine e restituisce:
    - Il dizionario delle box etichettate
    - Il percorso dell'immagine mascherata salvata
    """
    root = tk.Tk()
    creator = LabelCreator(root, image_path, target_output, augmentations)
    root.mainloop()
    boxes = creator.get_boxes()
    masked_image = creator.masked_image_path
    root.destroy()
    return boxes, masked_image
