import os
import time
import shutil
import socket
import threading
import numpy as np
import cv2
from datetime import datetime

# --- KONFIGURACJA ---
DWN_PATH = "./download"
PRC_PATH = "./processed"
STAGE_PATH = "./stage"
UDP_IPC_PORT = 5006
OSC_IP = "127.0.0.1"  # Pozostawione dla logiki UDP

TARGET_W, TARGET_H = 1792, 2400
INTERVAL = 30
FADE_DURATION = 3.0
MAX_DOWNLOAD_SLOTS = 6
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png')
WINDOW_NAME = "Projektor / Wyswietlacz"


class ImageItem:
    def __init__(self, filename):
        self.filename = filename
        self.was_streamed = False
        self.creation_time = os.path.getctime(os.path.join(DWN_PATH, filename))


class SimpleDisplayManager:
    def __init__(self):
        self.queue = []
        self.lock = threading.Lock()
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Obraz renderowany w oknie (aktualny stan)
        self.current_frame = np.zeros((TARGET_H, TARGET_W, 3), dtype=np.uint8)
        self.is_fading = False

        # Inicjalizacja folderów
        for p in [DWN_PATH, PRC_PATH, STAGE_PATH]:
            os.makedirs(p, exist_ok=True)

        # Tworzenie okna OpenCV (ustawione na pełny ekran lub normalne)
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_NAME, TARGET_W // 2, TARGET_H // 2)  # Podgląd proporcjonalny

    def is_file_ready(self, filepath, wait_time=0.5):
        try:
            if not os.path.exists(filepath):
                return False
            size_1 = os.path.getsize(filepath)
            time.sleep(wait_time)
            size_2 = os.path.getsize(filepath)
            return size_1 == size_2 and size_1 > 0
        except Exception:
            return False

    def update_file_system(self):
        with self.lock:
            try:
                all_files_on_disk = [f for f in os.listdir(DWN_PATH) if f.lower().endswith(IMAGE_EXTENSIONS)]
                self.queue = [item for item in self.queue if item.filename in all_files_on_disk]
                current_filenames = {item.filename for item in self.queue}
                new_files_candidates = [f for f in all_files_on_disk if f not in current_filenames]
                new_files_candidates.sort(key=lambda x: os.path.getctime(os.path.join(DWN_PATH, x)))

                for f in new_files_candidates:
                    full_path = os.path.join(DWN_PATH, f)
                    if self.is_file_ready(full_path):
                        new_item = ImageItem(f)
                        if len(self.queue) == 0:
                            self.queue.append(new_item)
                        else:
                            insert_pos = 1
                            for i in range(1, len(self.queue)):
                                if not self.queue[i].was_streamed:
                                    insert_pos = i + 1
                                else:
                                    break
                            self.queue.insert(insert_pos, new_item)
                            print(f"[QUEUE] Nowy plik zweryfikowany: {f} na pozycji {insert_pos}")

                if len(self.queue) > MAX_DOWNLOAD_SLOTS:
                    candidates_to_archive = [i for i, item in enumerate(self.queue) if i != 0]
                    if candidates_to_archive:
                        idx_to_remove = min(candidates_to_archive, key=lambda i: self.queue[i].creation_time)
                        to_remove = self.queue.pop(idx_to_remove)
                        old_path = os.path.join(DWN_PATH, to_remove.filename)
                        new_path = os.path.join(PRC_PATH, to_remove.filename)
                        if os.path.exists(old_path):
                            try:
                                shutil.move(old_path, new_path)
                                print(f"[CLEANUP] Archiwizacja: {to_remove.filename}")
                            except Exception as e:
                                print(f"[CLEANUP ERR] {e}")
            except Exception as e:
                print(f"[UPDATE FS ERR] {e}")

    def broadcast_udp(self):
        if not self.queue:
            try:
                self.udp_sock.sendto(b"", (OSC_IP, UDP_IPC_PORT))
            except:
                pass
            return

        msg_parts = []
        with self.lock:
            for item in self.queue:
                status_val = "1" if not item.was_streamed else "0"
                msg_parts.append(f"{item.filename}:{status_val}")

        msg_string = ",".join(msg_parts)
        try:
            self.udp_sock.sendto(msg_string.encode('utf-8'), (OSC_IP, UDP_IPC_PORT))
        except Exception as e:
            print(f"[UDP-SEND ERR] {e}")

    def load_and_prepare_img(self, filename):
        """Ładuje obraz, skaluje go do wymiarów docelowych w standardowym BGR dla OpenCV."""
        src_path = os.path.join(DWN_PATH, filename)
        stage_path = os.path.join(STAGE_PATH, filename)

        shutil.copy2(src_path, stage_path)
        img = cv2.imread(stage_path)
        img = cv2.resize(img, (TARGET_W, TARGET_H))
        os.remove(stage_path)
        return img

    def execute_crossfade(self, old_frame, new_img):
        """Wątek realizujący płynne przenikanie klatek w oknie."""
        self.is_fading = True
        fps = 40  # Klatki na sekundę podczas efektu fade
        total_frames = int(FADE_DURATION * fps)
        delay = 1.0 / fps

        for i in range(total_frames + 1):
            alpha = i / total_frames  # Waga nowego obrazu (0.0 -> 1.0)
            beta = 1.0 - alpha  # Waga starego obrazu (1.0 -> 0.0)

            # Blendowanie: dst = src1 * alpha + src2 * beta + gamma
            blended = cv2.addWeighted(new_img, alpha, old_frame, beta, 0.0)

            with self.lock:
                self.current_frame = blended

            time.sleep(delay)

        self.is_fading = False
        print("[DISPLAY] Przejście zakończone.")

    def run(self):
        print("System wyświetlania uruchomiony.")

        # Załaduj pierwsze zdjęcie na start, jeśli istnieje
        self.update_file_system()
        if self.queue:
            self.current_frame = self.load_and_prepare_img(self.queue[0].filename)

        last_switch_time = time.time()

        while True:
            # 1. Logika plików i statusu UDP
            self.update_file_system()
            self.broadcast_udp()

            # 2. Wyświetlanie aktualnej klatki w oknie OpenCV
            with self.lock:
                cv2.imshow(WINDOW_NAME, self.current_frame)

            # Kluczowe dla OpenCV: odświeża okno i przechwytuje klawisze (wymaga małego timeoutu)
            # Jeśli wciśniesz 'q', program się zamknie
            if cv2.waitKey(20) & 0xFF == ord('q'):
                break

            if not self.queue:
                continue

            # 3. Logika Timera i Przełączania
            current_time = time.time()
            if current_time - last_switch_time > INTERVAL and not self.is_fading:
                with self.lock:
                    if len(self.queue) < 2:
                        last_switch_time = time.time()
                        continue

                    print(f"\n[EVENT] Zmiana zdjęcia: {datetime.now().strftime('%H:%M:%S')}")

                    # Rotacja kolejki
                    self.queue[0].was_streamed = True
                    first = self.queue.pop(0)
                    self.queue.append(first)

                    try:
                        # Pobieramy stan obecny (jako stary) i szykujemy nowy obraz
                        old_frame = self.current_frame.copy()
                        new_img = self.load_and_prepare_img(self.queue[0].filename)

                        # Uruchamiamy efekt przenikania w osobnym wątku, żeby nie blokować pętli okna
                        fade_thread = threading.Thread(target=self.execute_crossfade, args=(old_frame, new_img))
                        fade_thread.daemon = True
                        fade_thread.start()

                        last_switch_time = time.time()
                    except Exception as e:
                        print(f"[ERROR] Problem z załadowaniem obrazu: {e}")
                        last_switch_time = time.time()

        cv2.destroyAllWindows()


if __name__ == "__main__":
    manager = SimpleDisplayManager()
    manager.run()