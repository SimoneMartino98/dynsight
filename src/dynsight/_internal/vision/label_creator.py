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
        # Linee orizzontali e verticali per il feedback del cursore
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
        self.sidebar.grid_propagate(flag=False)

        # Pulsante Submit
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

        # Pulsante Close per chiudere l'applicazione
        self.close_button = tk.Button(
            self.sidebar,
            text="Close",
            command=self.close,
        )
        self.close_button.pack(pady=10, fill="x")

        # Variabili per la gestione del labelling
        self.start_x = None
        self.start_y = None
        self.current_box = None
        self.boxes = []  # Ogni box è un dizionario contenente coordinate relative e assolute

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
        # Aggiorna anche le linee guida
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
        # Calcola le coordinate minime e massime per la box
        x1, y1 = self.start_x, self.start_y
        x2, y2 = end_x, end_y
        abs_coords = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        # Calcola le coordinate relative normalizzate in base alla dimensione dell'immagine
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
        """Salva una nuova immagine in cui solo il contenuto delle box selezionate viene
        copiato nelle stesse posizioni (il resto viene riempito di bianco).
        """
        if not self.boxes:
            raise ValueError("Nessuna box etichettata.")

        original_image = Image.open(self.image_path)
        # Crea una nuova immagine con lo stesso formato e dimensioni dell'originale, con sfondo bianco
        output_image = Image.new(
            original_image.mode, original_image.size, color=(255, 255, 255)
        )

        # Per ogni box, ritaglia l'area dall'immagine originale e incollala nella stessa posizione
        for box in self.boxes:
            x1, y1, x2, y2 = box["abs_coords"]
            cropped = original_image.crop((x1, y1, x2, y2))
            output_image.paste(cropped, (x1, y1))

        output_image.save(output_path)
        print(f"Immagine salvata in {output_path}")

    def create_dataset(self, cutted_image_path: pathlib.Path) -> None:
        """Crea il dataset per il training YOLO:
        - Crea le directory per le immagini e le etichette (train e val)
        - Copia l'immagine tagliata nelle directory delle immagini
        - Suddivide le etichette in train e val
        - Crea un file YAML per la configurazione del training
        """
        # Percorsi di base per il dataset
        dataset_base = pathlib.Path("dataset_guess")
        yaml_file = pathlib.Path("dataset_guess.yaml")
        img_train = dataset_base / "images" / "train"
        img_val = dataset_base / "images" / "val"
        lab_train = dataset_base / "labels" / "train"
        lab_val = dataset_base / "labels" / "val"

        # Creazione delle directory, se non esistono
        for path in [img_train, img_val, lab_train, lab_val]:
            path.mkdir(parents=True, exist_ok=True)

        # Copia dell'immagine ritagliata, se esiste
        if cutted_image_path.is_file():
            destination_train = img_train / cutted_image_path.name
            destination_val = img_val / cutted_image_path.name
            destination_train.write_bytes(cutted_image_path.read_bytes())
            destination_val.write_bytes(cutted_image_path.read_bytes())

        # Ottiene le box etichettate e le suddivide in due metà per train/val
        boxes_list = list(self.get_boxes().values())
        split_index = len(boxes_list) // 2
        train_boxes = boxes_list[:split_index]
        val_boxes = boxes_list[split_index:]

        # Scrittura del file di etichette per il training (train/0.txt)
        output_file_train = lab_train / "0.txt"
        with output_file_train.open("w") as f:
            for box in train_boxes:
                f.write(
                    f"0 {box['center_x']:.6f} {box['center_y']:.6f} {box['width']:.6f} {box['height']:.6f}\n"
                )

        # Scrittura del file di etichette per la validazione (val/0.txt)
        output_file_val = lab_val / "0.txt"
        with output_file_val.open("w") as f:
            for box in val_boxes:
                f.write(
                    f"0 {box['center_x']:.6f} {box['center_y']:.6f} {box['width']:.6f} {box['height']:.6f}\n"
                )

        # Creazione del file YAML con le opzioni di training
        with yaml_file.open("w") as f:
            f.write(f"train: {img_train!s}\n")
            f.write(f"val: {img_val!s}\n")
            f.write("nc: 1\n")
            f.write("names: ['object']\n")
        print("Dataset e file YAML creati correttamente.")

    def submit(self) -> None:
        """Quando viene premuto il pulsante Submit:
        - Salva l'immagine contenente solo il contenuto delle box selezionate
        - Crea il dataset per il training YOLO
        - Chiude l'applicazione
        """
        try:
            output_path = self.image_path.parent / "cutted" / "0.jpg"
            # Assicurati che la directory 'cutted' esista
            output_path.parent.mkdir(parents=True, exist_ok=True)
            self.save_selected_boxes(output_path)
            self.create_dataset(output_path)
        except Exception as e:
            print(
                f"Errore durante il salvataggio o la creazione del dataset: {e}"
            )
        finally:
            self.master.quit()

    def get_boxes(self) -> dict:
        """Restituisce le box etichettate in forma di dizionario."""
        return {box["id"]: box for box in self.boxes}

    def undo(self) -> None:
        """Annulla l'ultima box etichettata."""
        if self.boxes:
            last_box = self.boxes.pop()
            self.canvas.delete(last_box["id"])

    def close(self) -> None:
        """Chiude l'applicazione."""
        self.master.quit()
