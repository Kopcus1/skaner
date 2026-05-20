import cv2
import numpy as np
import os
import shutil
import time
import sys

# --- KONFIGURACJA ŚCIEŻEK ---
CURRENT_DIR = os.getcwd()
BASE_DIR = os.path.join(CURRENT_DIR, "content")
INPUT_FOLDER = os.path.join(BASE_DIR, "CROPPED")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "AI_INPUT")
EXTRA_OUTPUT_ROOT = os.path.join(CURRENT_DIR, "full_content")
ARCHIVE_FOLDER = os.path.join(BASE_DIR, "CROPPED_ARCHIVE")

# PLIK MASKI (RAMY)
MASK_FILE = "mask.png"


class SimpleWindowMapper:
    def __init__(self):
        self.ensure_dirs()
        self.mask_img = self.load_mask()

    def ensure_dirs(self):
        paths = [INPUT_FOLDER, OUTPUT_FOLDER, ARCHIVE_FOLDER, EXTRA_OUTPUT_ROOT]
        for path in paths:
            if not os.path.exists(path):
                os.makedirs(path)
                print(f"[INIT] Utworzono folder: {path}")

    def load_mask(self):
        if not os.path.exists(MASK_FILE):
            print(f"[CRITICAL] Brak pliku maski: {MASK_FILE}!")
            sys.exit(1)

        # Wczytujemy z kanałem Alpha (IMREAD_UNCHANGED)
        mask = cv2.imread(MASK_FILE, cv2.IMREAD_UNCHANGED)
        if mask is None:
            print(f"[CRITICAL] Nie mozna odczytac {MASK_FILE}!")
            sys.exit(1)

        print(f"[INIT] Załadowano maskę: {mask.shape[1]}x{mask.shape[0]}")
        return mask

    def center_image(self, img_src, target_w, target_h):
        """Wstawia img_src w środek płótna."""
        h, w = img_src.shape[:2]

        # Skalowanie, aby obraz nie był większy niż maska
        scale = min(target_w / w, target_h / h)
        if scale < 1.0:
            img_src = cv2.resize(img_src, (int(w * scale), int(h * scale)))
            h, w = img_src.shape[:2]

        # Tworzymy czarne tło (płótno)
        canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)

        # Logika wyśrodkowania (z opcjonalną korektą przesunięcia)
        base_x = (target_w - w) // 2
        base_y = (target_h - h) // 2

        # Zabezpieczenie zakresu (OpenCV ROI)
        x_offset = max(0, min(base_x, target_w - w))
        y_offset = max(0, min(base_y, target_h - h)) - 59

        # Wklejanie zdjęcia na płótno
        canvas[y_offset:y_offset + h, x_offset:x_offset + w] = img_src
        return canvas

    def process_file(self, filename):
        src_path = os.path.join(INPUT_FOLDER, filename)
        time.sleep(0.2)

        img_src = cv2.imread(src_path)
        if img_src is None:
            return False

        # --- ROZCIĄGANIE O 0.5% NA OSI Y ---
        h, w = img_src.shape[:2]
        new_h = int(h * 0.99)  # Zwiększamy wysokość o 0.5%
        new_w = int(w* 0.99)  # Zwiększamy wysokość o 0.5%

        # Skalujemy obraz (w zostaje bez zmian, h rośnie)
        img_src = cv2.resize(img_src, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        # ----------------------------------

        m_h, m_w = self.mask_img.shape[:2]
        drawing_layer = self.center_image(img_src, m_w, m_h)

        # Alpha Blending (Nakładanie przezroczystej maski na wycentrowany rysunek)
        if self.mask_img.shape[2] == 4:
            b, g, r, a = cv2.split(self.mask_img)
            foreground = cv2.merge((b, g, r))
            alpha = a.astype(float) / 255.0
            alpha = cv2.merge((alpha, alpha, alpha))

            # Mieszanie warstw
            final_image = (foreground.astype(float) * alpha) + \
                          (drawing_layer.astype(float) * (1.0 - alpha))
            canvas = final_image.astype(np.uint8)
        else:
            # Fallback jeśli maska nie ma kanału Alpha
            canvas = self.mask_img[:, :, :3]

        # Zapisywanie wyników
        output_path = os.path.join(OUTPUT_FOLDER, filename)
        cv2.imwrite(output_path, canvas)

        # Dodatkowy zapis do struktury folderów sesji
        folder_name = os.path.splitext(filename)[0]
        extra_dir = os.path.join(EXTRA_OUTPUT_ROOT, folder_name)
        os.makedirs(extra_dir, exist_ok=True)
        cv2.imwrite(os.path.join(extra_dir, "wrap.jpg"), canvas)

        # Archiwizacja oryginału
        shutil.move(src_path, os.path.join(ARCHIVE_FOLDER, filename))
        print(f"[OK] Przetworzono: {filename}")
        return True

    def run_loop(self):
        print("--- SIMPLE MASK COMPOSITOR START ---")
        try:
            while True:
                files = [f for f in os.listdir(INPUT_FOLDER)
                         if f.lower().endswith(('.jpg', '.png', '.jpeg'))]

                if not files:
                    time.sleep(1)
                    continue

                for filename in files:
                    self.process_file(filename)
        except KeyboardInterrupt:
            print("\n[STOP] Zamykanie programu...")


if __name__ == "__main__":
    app = SimpleWindowMapper()
    app.run_loop()