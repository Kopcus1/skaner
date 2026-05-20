import os
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageDraw, ImageFont


class ImageGeneratorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Generator Liczb PNG")
        self.root.geometry("300x200")

        self.counter = 1
        self.output_dir = ""

        # UI
        tk.Label(root, text="Folder zapisu:", font=("Arial", 10, "bold")).pack(pady=5)
        self.dir_label = tk.Label(root, text="Nie wybrano...", fg="gray")
        self.dir_label.pack(pady=5)

        tk.Button(root, text="Wybierz folder", command=self.select_folder).pack(pady=5)
        self.btn_add = tk.Button(root, text="DODAJ PLIK (1)", command=self.generate_image,
                                 state="disabled", bg="green", fg="white", font=("Arial", 12, "bold"))
        self.btn_add.pack(pady=20)

    def select_folder(self):
        self.output_dir = filedialog.askdirectory()
        if self.output_dir:
            self.dir_label.config(text=os.path.basename(self.output_dir), fg="black")
            self.btn_add.config(state="normal")
            # Sprawdź numerację w folderze, żeby nie nadpisywać
            self.sync_counter()

    def sync_counter(self):
        existing_files = [f for f in os.listdir(self.output_dir) if f.startswith("num_") and f.endswith(".png")]
        if existing_files:
            nums = [int(f.split("_")[1].split(".")[0]) for f in existing_files if
                    f.split("_")[1].split(".")[0].isdigit()]
            if nums:
                self.counter = max(nums) + 1
                self.btn_add.config(text=f"DODAJ PLIK ({self.counter})")

    def generate_image(self):
        # Konfiguracja obrazu (1792x2400 tak jak w Twoim systemie NDI)
        w, h = 1792, 2400
        img = Image.new('RGBA', (w, h), color=(0, 0, 0, 0))  # Przezroczyste tło
        draw = ImageDraw.Draw(img)

        # Próba załadowania czcionki systemowej (Arial)
        try:
            # Zwiększ rozmiar czcionki (np. 1000)
            font = ImageFont.truetype("arial.ttf", 1000)
        except:
            font = ImageFont.load_default()

        text = str(self.counter)

        # Centrowanie tekstu
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((w - tw) / 2, (h - th) / 2), text, fill="white", font=font)

        # Zapis
        filename = f"num_{self.counter}.png"
        filepath = os.path.join(self.output_dir, filename)
        img.save(filepath)

        print(f"Wygenerowano: {filename}")
        self.counter += 1
        self.btn_add.config(text=f"DODAJ PLIK ({self.counter})")


if __name__ == "__main__":
    root = tk.Tk()
    app = ImageGeneratorApp(root)
    root.mainloop()