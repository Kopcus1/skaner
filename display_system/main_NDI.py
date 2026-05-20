import os
import time
import shutil
import socket
import json
import threading
import numpy as np
import cv2
import NDIlib as ndi
from datetime import datetime
from pythonosc import udp_client


# --- KONFIGURACJA ---
DWN_PATH = "./download"
PRC_PATH = "./processed"
STAGE_PATH = "./stage"
OSC_IP = "127.0.0.1"
OSC_PORT = 8010
UDP_IPC_PORT = 5006

TARGET_W, TARGET_H = 1792, 2400
INTERVAL = 30
FADE_DURATION = 3.0
MAX_DOWNLOAD_SLOTS = 6
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png')


class ImageItem:
    def __init__(self, filename):
        self.filename = filename
        self.was_streamed = False
        self.creation_time = os.path.getctime(os.path.join(DWN_PATH, filename))

    def to_dict(self):
        return {"file": self.filename, "streamed": int(self.was_streamed)}


class NDI_UDP_Manager:
    def __init__(self):
        self.queue = []
        self.current_stream_idx = 0
        self.lock = threading.Lock()
        self.last_switch_time = time.time()
        self.osc_client = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)


        # Inicjalizacja folderów
        for p in [DWN_PATH, PRC_PATH, STAGE_PATH]:
            os.makedirs(p, exist_ok=True)

        if not ndi.initialize():
            raise RuntimeError("NDI nie mogło zostać zainicjowane")

            # Próba najbardziej kompatybilnego stworzenia sendera
            # Tworzymy pusty obiekt ustawień i przypisujemy mu nazwę
        settings_a = ndi.SendCreate()
        settings_a.ndi_name = "Stream_A"

        settings_b = ndi.SendCreate()
        settings_b.ndi_name = "Stream_B"

        try:
            self.ndi_senders = [
                ndi.send_create(settings_a),
                ndi.send_create(settings_b)
            ]
        except Exception as e:
            print(f"[NDI ERR] Problem przy tworzeniu senderów: {e}")

        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def is_file_ready(self, filepath, wait_time=0.5):
        """Sprawdza czy plik został w pełni zapisany na dysku."""
        try:
            if not os.path.exists(filepath):
                return False

            # Pierwszy pomiar rozmiaru
            size_1 = os.path.getsize(filepath)
            time.sleep(wait_time)
            # Drugi pomiar rozmiaru
            size_2 = os.path.getsize(filepath)

            # Jeśli rozmiary są identyczne i większe od 0, plik jest gotowy
            return size_1 == size_2 and size_1 > 0
        except Exception:
            return False

    def update_file_system(self):
        """Zarządza kolejką plików, dodając nowe zdjęcia dopiero po ich pełnym zapisaniu."""
        with self.lock:
            try:
                # 1. Pobierz listę wszystkich zdjęć na dysku
                all_files_on_disk = [f for f in os.listdir(DWN_PATH) if f.lower().endswith(IMAGE_EXTENSIONS)]

                # 2. Usuń z kolejki obiekty, których nie ma już fizycznie w folderze
                self.queue = [item for item in self.queue if item.filename in all_files_on_disk]

                # 3. Zidentyfikuj pliki, których nie ma jeszcze w kolejce
                current_filenames = {item.filename for item in self.queue}
                new_files_candidates = [f for f in all_files_on_disk if f not in current_filenames]

                # Sortujemy kandydatów po dacie utworzenia (najstarsze pierwsze)
                new_files_candidates.sort(key=lambda x: os.path.getctime(os.path.join(DWN_PATH, x)))

                for f in new_files_candidates:
                    full_path = os.path.join(DWN_PATH, f)

                    # --- KLUCZOWY DELAY I WERYFIKACJA ---
                    # Sprawdzamy czy plik jest gotowy zanim stworzymy obiekt ImageItem
                    if self.is_file_ready(full_path):
                        new_item = ImageItem(f)

                        if len(self.queue) == 0:
                            self.queue.append(new_item)
                        else:
                            # Szukamy miejsca ZA ostatnim nieodtworzonym zdjęciem
                            # Index 0 to zawsze to, co "leci" teraz, więc szukamy od Index 1
                            insert_pos = 1
                            for i in range(1, len(self.queue)):
                                if not self.queue[i].was_streamed:
                                    insert_pos = i + 1
                                else:
                                    # Znaleźliśmy stare (streamed) zdjęcie, wskakujemy przed nie
                                    break

                            self.queue.insert(insert_pos, new_item)
                            print(f"[QUEUE] Nowy plik zweryfikowany i dodany: {f} na pozycję {insert_pos}")
                    else:
                        # Plik jeszcze w trakcie transferu, pomijamy go w tej iteracji
                        # Zostanie sprawdzony ponownie przy następnym wywołaniu funkcji
                        pass

                # 4. Zarządzanie limitem (MAX_DOWNLOAD_SLOTS = 6)
                if len(self.queue) > MAX_DOWNLOAD_SLOTS:
                    # Nie usuwamy Index 0 (aktualnie nadawanego)
                    # Szukamy najstarszego zdjęcia wśród archiwalnych (tych już wyświetlonych)
                    candidates_to_archive = [i for i, item in enumerate(self.queue) if i != 0]

                    if candidates_to_archive:
                        # Sortujemy kandydatów po dacie, by usunąć najstarszy fizycznie plik
                        idx_to_remove = min(candidates_to_archive, key=lambda i: self.queue[i].creation_time)
                        to_remove = self.queue.pop(idx_to_remove)

                        old_path = os.path.join(DWN_PATH, to_remove.filename)
                        new_path = os.path.join(PRC_PATH, to_remove.filename)

                        if os.path.exists(old_path):
                            try:
                                shutil.move(old_path, new_path)
                                print(f"[CLEANUP] Archiwizacja najstarszego pliku: {to_remove.filename}")
                            except Exception as e:
                                print(f"[CLEANUP ERR] Nie można przenieść pliku: {e}")

            except Exception as e:
                print(f"[UPDATE FS ERR] Błąd krytyczny: {e}")

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
                # 1 = fioletowa ramka (nowe), 0 = szara ramka (stare)
                status_val = "1" if not item.was_streamed else "0"
                msg_parts.append(f"{item.filename}:{status_val}")

        msg_string = ",".join(msg_parts)
        # USUNIĘTO: + f"|{self.last_switch_time}" - to psuło parsowanie w skanerze

        try:
            self.udp_sock.sendto(msg_string.encode('utf-8'), (OSC_IP, UDP_IPC_PORT))
        except Exception as e:
            print(f"[UDP-SEND ERR] {e}")

    def run(self):
        print(f"System NDI/UDP uruchomiony.")
        print(f"Interwał zmiany: {INTERVAL}s | Rozdzielczość: {TARGET_W}x{TARGET_H}")

        # Inicjalizacja czasu startu
        self.last_switch_time = time.time()
        last_check_time = 0

        while True:
            # 1. Zarządzanie plikami i wysyłka statusu UDP
            # Robimy to w każdej pętli, aby skaner miał świeży timestamp dla paska
            self.update_file_system()
            self.broadcast_udp()

            if not self.queue:
                if time.time() - last_check_time > 5:
                    print("[IDLE] Oczekiwanie na zdjęcia w folderze download...")
                    last_check_time = time.time()
                time.sleep(1)
                continue

                # 2. Logika przełączania (Główny Timer)
            current_time = time.time()
            if current_time - self.last_switch_time > INTERVAL:
                with self.lock:
                    if len(self.queue) < 2:
                            # Jeśli jest tylko 1 zdjęcie, nie ma na co przełączyć
                        self.last_switch_time = time.time()
                        continue

                    print(f"\n[EVENT] Zmiana zdjęcia: {datetime.now().strftime('%H:%M:%S')}")

                        # A. Rotacja kolejki
                    self.queue[0].was_streamed = True
                    first = self.queue.pop(0)
                    self.queue.append(first)

                        # B. Przygotowanie obrazu dla NOWEGO zdjęcia na indexie 0
                    try:
                        next_item = self.queue[0]
                        img_data = self.prepare_frame(next_item.filename)

                            # C. Wybór ALTERNATYWNEGO streamu (Flip-Flop)
                            # Jeśli current był 0, to target jest 1.
                        target_stream_idx = 1 if self.current_stream_idx == 0 else 0

                            # D. Wysyłka NDI
                        video_frame = ndi.VideoFrameV2()
                        video_frame.data = img_data
                        video_frame.FourCC = ndi.FOURCC_VIDEO_TYPE_BGRX
                        video_frame.xres = TARGET_W
                        video_frame.yres = TARGET_H
                        video_frame.line_stride_in_bytes = TARGET_W * 4

                        ndi.send_send_video_v2(self.ndi_senders[target_stream_idx], video_frame)

                            # E. Wyzwolenie OSC
                        self.execute_fade(target_stream_idx)

                            # F. AKTUALIZACJA STANU (żeby następnym razem wybrał ten drugi)
                        self.current_stream_idx = target_stream_idx
                        self.last_switch_time = time.time()

                        print(f"[NDI] Stream_{'A' if target_stream_idx == 0 else 'B'} nadaje: {next_item.filename}")

                    except Exception as e:
                        print(f"[ERROR] {e}")
                        self.last_switch_time = time.time()

    def prepare_frame(self, filename):
        """Ładuje i skaluje obraz do formatu NDI."""
        src_path = os.path.join(DWN_PATH, filename)
        stage_path = os.path.join(STAGE_PATH, filename)

        # Kopiowanie do stage (pkt 3)
        shutil.copy2(src_path, stage_path)

        img = cv2.imread(stage_path)
        img = cv2.resize(img, (TARGET_W, TARGET_H))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)

        # Po załadowaniu do RAM można usunąć ze stage
        os.remove(stage_path)
        return img

    def execute_fade(self, active_idx):
        """Uruchamia płynne przejście w osobnym wątku."""
        fade_thread = threading.Thread(target=self._smooth_fade_thread, args=(active_idx,))
        fade_thread.daemon = True  # Wątek zamknie się razem z programem
        fade_thread.start()

    def _smooth_fade_thread(self, target_idx):
        """Wątek realizujący płynne przejście opacity."""
        steps = 30  # Liczba kroków na sekundę
        total_steps = int(FADE_DURATION * steps)
        delay = FADE_DURATION / total_steps

        for i in range(total_steps + 1):
            # Obliczamy postęp od 0.0 do 1.0
            progress = i / total_steps

            # Jeśli target_idx == 0 (Stream A), to A rośnie, B maleje
            # Jeśli target_idx == 1 (Stream B), to B rośnie, A maleje
            val_a = progress if target_idx == 0 else (1.0 - progress)
            val_b = progress if target_idx == 1 else (1.0 - progress)

            try:
                self.osc_client.send_message("/surfaces/Quad-1/opacity", val_a)
                self.osc_client.send_message("/surfaces/Quad-2/opacity", val_b)
            except Exception as e:
                print(f"[OSC FADE ERR] {e}")

            time.sleep(delay)

        print(f"[OSC] Fade do Stream_{'A' if target_idx == 0 else 'B'} zakończony.")

if __name__ == "__main__":
    manager = NDI_UDP_Manager()
    manager.run()