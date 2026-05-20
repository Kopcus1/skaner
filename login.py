import cv2
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
import time
import sys
import os
import math
import threading
import json
from PIL import Image, ImageDraw, ImageFont
import socket

# --- KONFIGURACJA DEBUGOWANIA ---
sys.stdout.reconfigure(encoding='utf-8')

print("=========================================")
print("    SYSTEM WESOLA - MULTI-THREADED UI     ")
print("=========================================")

# --- KONFIGURACJA ŚCIEŻEK I PARAMETRÓW ---
CURRENT_DIR = os.getcwd()
BASE_DIR = os.path.join(CURRENT_DIR, "content")
OUTPUT_RAW_DIR = os.path.join(BASE_DIR, "RAW_PHOTO")
OUTPUT_CROPPED_DIR = os.path.join(BASE_DIR, "CROPPED")
EXTRA_OUTPUT_ROOT = os.path.join(CURRENT_DIR, "full_content")
FIREBASE_KEY_PATH = "serviceAccountKey.json"
COLLECTION_NAME = "qr_codes_skaner"

CAMERA_INDEX = 0
TRIGGER_TIME = 3
SUCCESS_DURATION = 10
ERROR_DURATION = 10
MIN_QR_COUNT = 3
MAX_SCANS = 300
SESSION_TIMEOUT = 60
MARKER_MEMORY_DURATION = 0.2
MAX_ABORTS_BEFORE_RESET = 3

FINAL_WIDTH = 1418
FINAL_HEIGHT = 1988

UI_CONFIG = {
    "IDLE": {"video": "HOME.mov", "show_cam": True},
    "LIMIT": {"video": "LIMIT.mov", "show_cam": False},
    "SCAN": {"video": "SCAN.mov", "show_cam": True},
    "LOADING": {"video": "LOADING.mov", "show_cam": False},
    "FAIL": {"video": "FAIL.mov", "show_cam": False},
    "SUCCESS": {"video": "SUKCES.mov", "show_cam": False}
}

CAM_CONFIG = {
    "IDLE": {"x": 1003, "y": 511, "w": 777, "h": 586, "r": 49},
    "SCAN": {"x": 571, "y": 341, "w": 777, "h": 589, "r": 47}
}


class PhotoQueueManager:
    def __init__(self, folder_path, max_h=235):  # Nieco większe miniatury
        self.folder_path = folder_path
        self.max_h = max_h
        self.cached_strip = None
        self._lock = threading.Lock()
        self.last_message = ""
        self.is_loading = False
        self.loading_start_time = 0
        self.loading_duration = 30.0
        self.first_thumb_width = 0
        self.label_w = 240  # Zwiększona szerokość na większy font

    def update_queue_from_names(self, filenames, statuses, raw_message):
        if raw_message == self.last_message:
            return
        self.last_message = raw_message
        self.is_loading = True
        self.loading_start_time = time.time()
        threading.Thread(target=self._build_cache, args=(filenames, statuses), daemon=True).start()

    def _build_cache(self, filenames, statuses):
        try:
            padding = 15
            arrow_w = 50
            text_area_h = 60  # Miejsce na napis nad zdjęciami

            thumbnails = []
            for fname in filenames:
                path = os.path.join(self.folder_path, fname)
                if not os.path.exists(path): continue
                img = cv2.imread(path)
                if img is None: continue

                h, w = img.shape[:2]
                new_w = int(w * (self.max_h / h))
                img_res = cv2.resize(img, (new_w, self.max_h), interpolation=cv2.INTER_AREA)

                color = (255, 0, 255) if statuses.get(fname) else (140, 140, 140)
                cv2.rectangle(img_res, (0, 0), (new_w - 1, self.max_h - 1), color, 4)
                thumbnails.append((img_res, new_w))

            if not thumbnails:
                with self._lock: self.cached_strip = None
                return

            # Nowe obliczenia wymiarów:
            # Szerokość to suma zdjęć i strzałek (bez label_w obok)
            total_w = sum(t[1] for t in thumbnails) + (len(thumbnails) - 1) * arrow_w + padding * 2
            # Wysokość to wysokość zdjęcia + miejsce na tekst
            total_h = self.max_h + text_area_h

            # Tworzenie paska w PIL
            strip_pil = Image.new("RGB", (total_w, total_h), (0, 0, 0))
            draw = ImageDraw.Draw(strip_pil)

            try:
                font_path = os.path.join(os.getcwd(), "RobotoMono-SemiBold.ttf")
                font = ImageFont.truetype(font_path, 30) # Nieco mniejszy, by pasował w poziomie
            except:
                font = ImageFont.load_default()

            # Rysowanie napisu NA GÓRZE (wycentrowany lub od lewej)
            draw.text((padding, 5), "Kolejka: Queue:", fill=(255, 255, 255), font=font)

            # Konwersja do OpenCV
            strip = np.array(strip_pil)
            curr_x = padding
            # Zdjęcia zaczynają się od y = text_area_h
            img_y_offset = text_area_h

            for i, (t_img, t_w) in enumerate(thumbnails):
                if i == 0: self.first_thumb_width = t_w

                # Wklejanie zdjęcia z przesunięciem w dół
                strip[img_y_offset:img_y_offset + self.max_h, curr_x:curr_x + t_w] = t_img
                curr_x += t_w

                if i < len(thumbnails) - 1:
                    center_y = img_y_offset + (self.max_h // 2)
                    ax = curr_x + (arrow_w // 2)
                    pts = np.array([[ax + 12, center_y - 15], [ax - 8, center_y], [ax + 12, center_y + 15]], np.int32)
                    cv2.polylines(strip, [pts], False, (255, 255, 255), 4, cv2.LINE_AA)
                    curr_x += arrow_w

            with self._lock:
                self.cached_strip = strip

        except Exception as e:
            print(f"[PHOTO MGR ERR] {e}")

    def get_thumbnail_strip(self):
        with self._lock: return self.cached_strip

    def get_loading_progress(self):
        if not self.is_loading: return 0.0
        elapsed = time.time() - self.loading_start_time
        prog = min(elapsed / self.loading_duration, 1.0)
        if prog >= 1.0: self.is_loading = False
        return prog

class ThreadedVideoPlayer:
    def __init__(self, path, width, height, target_fps=60):
        self.path = path
        self.width = width
        self.height = height
        self.cap = cv2.VideoCapture(path)
        self.current_frame = np.zeros((height, width, 3), dtype=np.uint8)
        self.active = False
        self.running = True
        self.delay = 1.0 / target_fps
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        while self.running:
            if not self.active:
                time.sleep(0.1)
                continue
            start_time = time.time()
            ret, frame = self.cap.read()
            if not ret:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            self.current_frame = cv2.resize(frame, (self.width, self.height))
            elapsed = time.time() - start_time
            wait = self.delay - elapsed
            if wait > 0:
                time.sleep(wait)

    def stop(self):
        self.running = False
        self.cap.release()


class PreloadedVideoUIManager:
    def __init__(self, width=1920, height=1200):
        self.width = width
        self.height = height
        self.current_state = "IDLE"
        self.next_state = None
        self.transition = "NONE"
        self.alpha = 1.0
        self.fade_speed = 0.05
        self.black_frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self.bg_frame = np.full((height, width, 3), 0, dtype=np.uint8)

        # Słownik przechowujący wątkowe odtwarzacze dla każdego stanu
        self.players = {}
        for state, config in UI_CONFIG.items():
            self.players[state] = ThreadedVideoPlayer(config["video"], width, height)

        # Aktywujemy odtwarzacz dla stanu początkowego
        if self.current_state in self.players:
            self.players[self.current_state].active = True

    def get_render_frame(self, camera_frame_raw=None, stabilized_markers=None, progress=0.0, show_roi_debug=False,
                         roi_data=None):
        # 1. Pobieramy aktualną klatkę z aktywnego odtwarzacza wideo
        active_player = self.players.get(self.current_state)
        if active_player:
            render_frame = active_player.current_frame.copy()
        else:
            render_frame = self.bg_frame.copy()

        # 2. Nakładamy podgląd kamery, jeśli stan tego wymaga
        if UI_CONFIG[self.current_state]["show_cam"] and camera_frame_raw is not None:
            cfg = CAM_CONFIG.get(self.current_state, CAM_CONFIG["IDLE"])
            try:
                # Pobieramy wymiary surowej klatki z kamery
                h_raw, w_raw = camera_frame_raw.shape[:2]

                # Wymiary docelowe z konfiguracji UI
                w_target, h_target = cfg['w'], cfg['h']

                # Obliczamy proporcje
                aspect_raw = w_raw / h_raw
                aspect_target = w_target / h_target

                if aspect_raw > aspect_target:
                    # Obraz z kamery jest szerszy niż okno docelowe -> przycinamy boki
                    w_crop = int(h_raw * aspect_target)
                    offset_x = (w_raw - w_crop) // 2
                    offset_y = 0
                    cropped_cam = camera_frame_raw[:, offset_x:offset_x + w_crop]
                else:
                    # Obraz z kamery jest wyższy niż okno docelowe -> przycinamy górę i dół
                    h_crop = int(w_raw / aspect_target)
                    offset_x = 0
                    offset_y = (h_raw - h_crop) // 2
                    cropped_cam = camera_frame_raw[offset_y:offset_y + h_crop, :]

                # Zmieniamy rozmiar JUŻ PRZYCIĘTEGO obrazu (brak deformacji)
                cam_view = cv2.resize(cropped_cam, (w_target, h_target))

                # Dynamiczne skalowanie dla markerów i ROI oparte o WYCIĘTY obraz
                h_c, w_c = cropped_cam.shape[:2]
                scale_x = w_target / w_c
                scale_y = h_target / h_c

                # Logika rysowania HUD dla stanu SCAN
                if self.current_state == "SCAN":
                    if stabilized_markers is not None:
                        for pos, coords in stabilized_markers.items():
                            # Korekta współrzędnych o wartość przycięcia tła
                            px = int((coords[0] - offset_x) * scale_x)
                            py = int((coords[1] - offset_y) * scale_y)

                            # Rysuj tylko, jeśli punkt mieści się w widocznym oknie podglądu
                            if 0 <= px < w_target and 0 <= py < h_target:
                                cv2.circle(cam_view, (px, py), 12, (0, 255, 0), -1)
                                cv2.circle(cam_view, (px, py), 15, (255, 255, 255), 2)

                    if progress > 0:
                        bar_w = int(w_target * float(progress))
                        if bar_w > 0:
                            cv2.rectangle(cam_view, (0, h_target - 15), (bar_w, h_target), (0, 255, 255), -1)

                # --- NOWE MIEJSCE RYSOWANIA ROI (PRZED NAŁOŻENIEM MASKI) ---
                if show_roi_debug and roi_data:
                    rx = int((roi_data.get('roi_x', 0) - offset_x) * scale_x)
                    ry = int((roi_data.get('roi_y', 0) - offset_y) * scale_y)
                    rw = int(roi_data.get('roi_w', 100) * scale_x)
                    rh = int(roi_data.get('roi_h', 100) * scale_y)

                    # Rysujemy fioletowy prostokąt bezpośrednio na widoku z kamery
                    cv2.rectangle(cam_view, (rx, ry), (rx + rw, ry + rh), (255, 0, 255), 3)
                  #  cv2.putText(cam_view, "ROI DEBUG", (rx + 5, ry + 25),
                   #             cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2, cv2.LINE_AA)

                # Wycinanie maski i nakładanie zaokrąglonego okna na render_frame
                mask_circle = self._get_cached_rounded_mask(w_target, h_target, cfg['r'])
                roi = render_frame[cfg['y']:cfg['y'] + h_target, cfg['x']:cfg['x'] + w_target]
                bg_part = cv2.bitwise_and(roi, roi, mask=cv2.bitwise_not(mask_circle))
                fg_part = cv2.bitwise_and(cam_view, cam_view, mask=mask_circle)
                render_frame[cfg['y']:cfg['y'] + h_target, cfg['x']:cfg['x'] + w_target] = cv2.add(bg_part, fg_part)

            except Exception as e:
                print(f"[UI RENDER ERR] {e}")
                pass

        # 3. Obsługa przejścia Fade
        if self.transition != "NONE":
            if self.transition == "FADE_OUT":
                self.alpha -= self.fade_speed
                if self.alpha <= 0:
                    self.alpha = 0
                    if self.current_state in self.players:
                        self.players[self.current_state].active = False
                    self.current_state = self.next_state
                    if self.current_state in self.players:
                        self.players[self.current_state].active = True
                    self.transition = "FADE_IN"
            elif self.transition == "FADE_IN":
                self.alpha += self.fade_speed
                if self.alpha >= 1.0:
                    self.alpha = 1.0
                    self.transition = "NONE"
            render_frame = cv2.addWeighted(render_frame, self.alpha, self.black_frame, 1 - self.alpha, 0)

        return render_frame

    def _get_cached_rounded_mask(self, w, h, r):
        if not hasattr(self, '_mask_cache'): self._mask_cache = {}
        key = (w, h, r)
        if key not in self._mask_cache:
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.rectangle(mask, (r, 0), (w - r, h), 255, -1)
            cv2.rectangle(mask, (0, r), (w, h - r), 255, -1)
            cv2.circle(mask, (r, r), r, 255, -1)
            cv2.circle(mask, (w - r, r), r, 255, -1)
            cv2.circle(mask, (r, h - r), r, 255, -1)
            cv2.circle(mask, (w - r, h - r), r, 255, -1)
            self._mask_cache[key] = mask
        return self._mask_cache[key]

    def change_state(self, new_state):
        # Jeśli stan jest ten sam, nic nie rób
        if new_state == self.current_state:
            return

        # Pozwalamy na zmianę stanu nawet w trakcie trwania przejścia (FADE)
        self.next_state = new_state
        self.transition = "FADE_OUT"
        # Opcjonalnie: przyspiesz fade dla dynamicznych zmian, np. self.fade_speed = 0.15


class VideoCaptureThread(threading.Thread):
    def __init__(self, index):
        super().__init__(daemon=True)
        self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 2400)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1792)
        self.frame = None
        self.ret = False
        self.running = True

    def run(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                self.frame = frame
                self.ret = ret
            else:
                time.sleep(0.01)

    def stop(self):
        self.running = False
        self.cap.release()


    # --- GLOBALNE FUNKCJE POMOCNICZE GEOMETRII ---
def estimate_missing_point(markers):
    """
    Oblicza pozycję 4. punktu na podstawie 3 istniejących.
    """
    updated_markers = markers.copy()
    estimated_key = None

    if 'TL' not in markers and all(k in markers for k in ['TR', 'BR', 'BL']):
        x = markers['TR'][0] + markers['BL'][0] - markers['BR'][0]
        y = markers['TR'][1] + markers['BL'][1] - markers['BR'][1]
        updated_markers['TL'] = (int(x), int(y))
        estimated_key = 'TL'
    elif 'TR' not in markers and all(k in markers for k in ['TL', 'BR', 'BL']):
        x = markers['TL'][0] + markers['BR'][0] - markers['BL'][0]
        y = markers['TL'][1] + markers['BR'][1] - markers['BL'][1]
        updated_markers['TR'] = (int(x), int(y))
        estimated_key = 'TR'
    elif 'BR' not in markers and all(k in markers for k in ['TL', 'TR', 'BL']):
        x = markers['TR'][0] + markers['BL'][0] - markers['TL'][0]
        y = markers['TR'][1] + markers['BL'][1] - markers['TL'][1]
        updated_markers['BR'] = (int(x), int(y))
        estimated_key = 'BR'
    elif 'BL' not in markers and all(k in markers for k in ['TL', 'TR', 'BR']):
        x = markers['TL'][0] + markers['BR'][0] - markers['TR'][0]
        y = markers['TL'][1] + markers['BR'][1] - markers['TR'][1]
        updated_markers['BL'] = (int(x), int(y))
        estimated_key = 'BL'

    return updated_markers, estimated_key

class SmartScanner:
    def __init__(self):
        print(f"[INIT] Start wątku kamery: {CAMERA_INDEX}")
        self.cam = VideoCaptureThread(CAMERA_INDEX)
        self.cam.start()

        self.elapsed_before_pause = 0
        self.waiting_for_ai = False

        self.last_known_positions = {}  # Pamięć pozycji: {'TL': (x, y), ...}
        self.marker_types = {}  # Typ markera dla HUD: {'TL': 'REAL' lub 'RECOVERED'}
        self.POS_THRESHOLD = 0.10  # 10% marginesu błędu ruchu

        self.setup_directories()
        self.init_firestore()
        self.detector = cv2.QRCodeDetector()
        self.ui_manager = PreloadedVideoUIManager()
        self.load_roi_config()

        self.current_user_id = None
        self.current_client_name = ""
        self.current_user_scans = 0
        self.last_qr_data = ""
        self.qr_cooldown = 0
        self.last_activity_time = 0
        self.timer_start = None
        self.success_timer_start = None
        self.error_timer_start = None
        self.scan_abort_count = 0

        self.latest_raw_markers = {}
        self.latest_long_codes = []
        self.marker_history = {}

        self.show_roi_debug = True  # Domyślnie prostokąt jest ukryty

        self.ui_state = {
            "status_header": "ZABLOKOWANY",
            "status_sub": "Zeskanuj bilet",
            "bg_color": "blue",
            "progress": 0.0,
            "user_name": "",
            "scan_count": 0,
            "max_scans": MAX_SCANS,
            "is_logged_in": False
        }

        self.detection_thread = threading.Thread(target=self.qr_worker, daemon=True)
        self.detection_thread.start()

        self.photo_manager = PhotoQueueManager("./display_system/download")
        self.udp_port = 5006
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.bind(("127.0.0.1", self.udp_port))
        self.udp_sock.setblocking(False)

        self.current_new_id = ""

    def setup_directories(self):
        for path in [OUTPUT_RAW_DIR, OUTPUT_CROPPED_DIR, EXTRA_OUTPUT_ROOT]:
            os.makedirs(path, exist_ok=True)

    def init_firestore(self):
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(FIREBASE_KEY_PATH)
                firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            print("[DB] Polaczono.")
        except Exception as e:
            print(f"[DB ERR] {e}")

    def load_roi_config(self):
        self.use_roi = False
        self.roi_data = None
        config_path = "roi_config.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    self.roi_data = json.load(f)
                required_keys = ['roi_x', 'roi_y', 'roi_w', 'roi_h']
                if all(k in self.roi_data for k in required_keys):
                    self.use_roi = True
                    print(f"[ROI] Aktywny: {self.roi_data['roi_w']}x{self.roi_data['roi_h']}")
            except Exception as e:
                print(f"[ROI ERR] {e}")

    def qr_worker(self):
        print("[THREAD] Detekcja QR aktywna (nowy format: POZ_ID_NOWEID).")
        while True:
            time.sleep(0.03)
            if self.ui_manager.current_state not in ["SCAN", "IDLE", "LOADING"] or not self.cam.ret:
                self.latest_raw_markers = {}
                continue

            frame_copy = self.cam.frame.copy()
            ox, oy = 0, 0

            if self.use_roi and self.roi_data:
                r = self.roi_data
                ys, ye = max(0, r['roi_y']), min(frame_copy.shape[0], r['roi_y'] + r['roi_h'])
                xs, xe = max(0, r['roi_x']), min(frame_copy.shape[1], r['roi_x'] + r['roi_w'])
                analysis_frame = frame_copy[ys:ye, xs:xe]
                ox, oy = xs, ys
            else:
                analysis_frame = frame_copy

            gray = cv2.cvtColor(analysis_frame, cv2.COLOR_BGR2GRAY)
            ok, decoded, points, _ = self.detector.detectAndDecodeMulti(gray)

            new_markers = {}
            unidentified_pts = []
            found_data = []
            current_types = {}
            dist_limit = max(gray.shape) * self.POS_THRESHOLD

            if ok and points is not None:
                for i, data in enumerate(decoded):
                    if not data: continue
                    pts = points[i].astype(int)
                    center = (int(np.mean(pts[:, 0]) + ox), int(np.mean(pts[:, 1]) + oy))

                    parts = data.split("_")
                    if len(parts) == 3:
                        pos = parts[0].upper().strip()
                        f_id = parts[1].strip()
                        n_id = parts[2].strip()

                        new_markers[pos] = center
                        current_types[pos] = 'REAL'
                        self.last_known_positions[pos] = center
                        found_data.append({"firestore_id": f_id, "new_id": n_id})
                    else:
                        unidentified_pts.append(center)

                for pt in unidentified_pts:
                    for pid, last_pos in self.last_known_positions.items():
                        if pid not in new_markers:
                            dist = math.sqrt((pt[0] - last_pos[0]) ** 2 + (pt[1] - last_pos[1]) ** 2)
                            if dist < dist_limit:
                                new_markers[pid] = pt
                                current_types[pid] = 'RECOVERED'
                                self.last_known_positions[pid] = pt
                                break

            self.latest_raw_markers = new_markers
            self.latest_long_codes = found_data
            self.marker_types = current_types

    def start_firestore_listener(self, doc_uuid):
        if not doc_uuid: return
        doc_ref = self.db.collection(COLLECTION_NAME).document(doc_uuid)

        def on_snapshot(doc_snapshot, changes, read_time):
            for doc in doc_snapshot:
                if not doc.exists: continue
                data = doc.to_dict()
                sg = data.get("StainedGlass", {})
                status = sg.get("status")
                if status == "processing":
                    self.ui_manager.change_state("LOADING")
                elif status == "ready":
                    self.waiting_for_ai = False
                    self.ui_manager.change_state("SUCCESS")
                    self.success_timer_start = time.time()
                elif status == "error":
                    self.waiting_for_ai = False
                    self.ui_manager.change_state("FAIL")
                    self.error_timer_start = time.time()

        doc_ref.on_snapshot(on_snapshot)

    def trigger_scan_procedure(self, frame, markers):
        self.waiting_for_ai = True
        self.latest_raw_markers = {}
        self.elapsed_before_pause = 0
        self.marker_history = {}
        self.timer_start = None
        user_id = self.current_user_id
        scan_nr = self.current_user_scans + 1
        try:
            self.db.collection(COLLECTION_NAME).document(user_id).update({
                "StainedGlass.status": "uploading",
                "StainedGlass.error_msg": ""
            })
        except Exception:
            self.waiting_for_ai = False
            self.ui_manager.change_state("FAIL")
            return
        self.current_user_scans += 1
        self.last_activity_time = time.time()
        self.start_firestore_listener(user_id)
        threading.Thread(target=self.process_and_save_task, args=(frame.copy(), markers, user_id, scan_nr),
                         daemon=True).start()
        self.ui_manager.change_state("LOADING")

    def handle_login_scan(self, data_dict):
        curr = time.time()
        f_id = data_dict["firestore_id"]
        n_id = data_dict["new_id"]

        # Cooldown i sprawdzenie czy to ten sam bilet
        if f_id == self.current_user_id and self.ui_state["is_logged_in"]: return
        if f_id == self.last_qr_data and (curr - self.qr_cooldown < 3.0): return

        self.last_qr_data = f_id
        self.qr_cooldown = curr

        exists, count, name = self.get_user_data(f_id)
        if exists:
            if count >= MAX_SCANS:
                self.error_timer_start = time.time()
                self.ui_manager.change_state("LIMIT")
                return

            self.current_user_id = f_id
            self.current_new_id = n_id  # NOWE POLE w klasie SmartScanner
            self.current_user_scans = count
            self.ui_state.update({"user_name": name, "scan_count": count, "is_logged_in": True})
            self.last_activity_time = time.time()

            if self.ui_manager.current_state != "SCAN":
                self.ui_manager.change_state("SCAN")

    def get_user_data(self, uuid):
        try:
            doc = self.db.collection(COLLECTION_NAME).document(uuid).get()
            if doc.exists:
                d = doc.to_dict()
                sg = d.get("StainedGlass", {})
                return True, sg.get("ScanCount", 0), d.get("ClientName", "Gość")
            return False, 0, ""
        except Exception:
            return False, 0, ""

    # --- TUŻ POD CAM_CONFIG ---

    def process_and_save_task(self, frame, markers, user_id, scan_nr):
        new_id = getattr(self, 'current_new_id', 'unknown')
        filename = f"{user_id}_{scan_nr}_{new_id}.jpg"

        try:
            # 1. Zapis RAW (Backup)
            cv2.imwrite(os.path.join(OUTPUT_RAW_DIR, filename), frame)

            # 2. Przygotowanie punktów do transformacji
            required = ['TL', 'TR', 'BR', 'BL']
            found = [k for k in required if k in markers]
            final_markers = markers.copy()

            if len(found) == 3:
                final_markers, _ = estimate_missing_point(final_markers)
            elif len(found) < 3:
                return

            src_pts = np.array([final_markers['TL'], final_markers['TR'], final_markers['BR'], final_markers['BL']],
                               dtype="float32")
            dst_pts = np.array(
                [[0, 0], [FINAL_WIDTH - 1, 0], [FINAL_WIDTH - 1, FINAL_HEIGHT - 1], [0, FINAL_HEIGHT - 1]],
                dtype="float32")

            # 3. Transformacja perspektywiczna
            M = cv2.getPerspectiveTransform(src_pts, dst_pts)
            warped = cv2.warpPerspective(frame, M, (FINAL_WIDTH, FINAL_HEIGHT))

            # 4. Przycinanie zgodnie z crop_config.json
            crop_path = "crop_config.json"
            if os.path.exists(crop_path):
                with open(crop_path, 'r') as f:
                    c = json.load(f)

                y_start = c.get("top", 0)
                y_end = FINAL_HEIGHT - c.get("bottom", 0)
                x_start = c.get("left", 0)
                x_end = FINAL_WIDTH - c.get("right", 0)

                if y_end > y_start and x_end > x_start:
                    warped = warped[y_start:y_end, x_start:x_end]

            # 5. Zapis FINAŁOWY
            cv2.imwrite(os.path.join(OUTPUT_CROPPED_DIR, filename), warped)

            # 6. Informacja dla bazy danych
            self.db.collection(COLLECTION_NAME).document(user_id).update({
                "StainedGlass.ScanCount": firestore.Increment(1)
            })
            print(f"[SUCCESS] Zapisano: {filename}")

        except Exception as e:
            print(f"[ERR PROCESS] {e}")

    def run_local_window(self):
        win_name = "System Wesola - Kiosk"
        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        frame_time = 1.0 / 60.0

        # Inicjalizacja pomocnicza dla stabilizacji
        if not hasattr(self, 'show_roi_debug'):
            self.show_roi_debug = False

        while True:
            start_loop = time.time()
            now = time.time()
            is_blocked = False

            # --- 1. ODBIÓR DANYCH UDP (KOLEJKA ZDJĘĆ) ---
            try:
                # Odbieramy wszystkie pakiety w buforze, interesuje nas ostatni stan
                last_packet = None
                while True:
                    data, addr = self.udp_sock.recvfrom(4096)
                    last_packet = data
            except BlockingIOError:

                if last_packet:
                    msg = last_packet.decode('utf-8', errors='ignore').strip()
                    # Jeśli w wiadomości jest znak |, bierzemy tylko to co przed nim
                    clean_msg = msg.split('|')[0]

                    raw_pairs = [p.strip() for p in clean_msg.split(',') if p.strip()]
                    clean_names = []
                    status_map = {}
                    for pair in raw_pairs:
                        if ":" in pair:
                            parts = pair.split(":")
                            # Bezpieczne pobieranie statusu (ostatni element)
                            status = parts[-1].strip()
                            fname = ":".join(parts[:-1])
                            cname = os.path.basename(fname)
                            clean_names.append(cname)
                            # Upewniamy się, że bierzemy tylko pierwszy znak statusu (na wypadek śmieci)
                            status_map[cname] = (status[0] == "1")

                    self.photo_manager.update_queue_from_names(clean_names, status_map, msg)

            # --- 2. LOGIKA BLOKOWANIA I STANÓW ---
            # --- 2. LOGIKA BLOKOWANIA I STANÓW ---
            if self.waiting_for_ai:
                is_blocked = True
            elif self.error_timer_start:
                if (now - self.error_timer_start < ERROR_DURATION):
                    is_blocked = True
                else:
                    self.error_timer_start = None
                    self.ui_state["is_logged_in"] = False
                    # --- POPRAWKA: Czyszczenie przed wejściem w IDLE ---
                    self.latest_raw_markers = {}
                    self.marker_history = {}
                    self.timer_start = None
                    self.elapsed_before_pause = 0
                    self.current_user_id = None
                    self.ui_manager.change_state("IDLE")
            elif self.success_timer_start:
                if (now - self.success_timer_start < SUCCESS_DURATION):
                    is_blocked = True
                else:
                    self.success_timer_start = None
                    self.ui_state["is_logged_in"] = False
                    # --- POPRAPKA: Czyszczenie przed wejściem w IDLE ---
                    self.latest_raw_markers = {}
                    self.marker_history = {}
                    self.timer_start = None
                    self.elapsed_before_pause = 0
                    self.current_user_id = None
                    self.ui_manager.change_state("IDLE")

            # --- 3. SKANOWANIE I LOGIN ---
            if not is_blocked and self.latest_long_codes:
                self.handle_login_scan(self.latest_long_codes[0])
                self.latest_long_codes = []

            # --- 4. STABILIZACJA I PROGRESS (Z POPRAWNĄ ESTYMACJĄ) ---
            for pos, coord in self.latest_raw_markers.items():
                self.marker_history[pos] = {'coord': coord, 'seen': now}

            # Krok A: Zbieramy punkty, które fizycznie widzieliśmy w ciągu ostatnich 0.2 sekundy
            stabilized = {}
            for pos in ['TL', 'TR', 'BL', 'BR']:
                if pos in self.marker_history and now - self.marker_history[pos][
                    'seen'] < MARKER_MEMORY_DURATION:
                    stabilized[pos] = self.marker_history[pos]['coord']

            # Krok B: Jeśli mamy stabilne 3 punkty, dopiero TERAZ matematycznie dorzucamy czwarty
            if len(stabilized) == 3:
                stabilized, est_key = estimate_missing_point(stabilized)
                if est_key:
                    self.marker_types[est_key] = 'ESTIMATED'

            display_progress = 0.0
            if not is_blocked and self.current_user_id:
                if now - self.last_activity_time > SESSION_TIMEOUT:
                    self.current_user_id = None
                    self.ui_manager.change_state("IDLE")

                # Warunek wyzwolenia: sprawdzamy 'stabilized' (który po estymacji ma już 4 punkty!)
                if len(stabilized) == 4:
                    if self.timer_start is None: self.timer_start = now
                    total = self.elapsed_before_pause + (now - self.timer_start)
                    display_progress = min(total / TRIGGER_TIME, 1.0)
                    if total >= TRIGGER_TIME:
                        self.trigger_scan_procedure(self.cam.frame.copy(), stabilized)
                        self.timer_start, self.elapsed_before_pause = None, 0
                else:
                    if self.timer_start is not None:
                        self.elapsed_before_pause += (now - self.timer_start)
                        self.timer_start = None
                    display_progress = min(self.elapsed_before_pause / TRIGGER_TIME, 1.0)

            # --- 5. RENDEROWANIE BAZOWEGO UI ---
            final_frame = self.ui_manager.get_render_frame(
                self.cam.frame,
                stabilized_markers=stabilized,
                progress=display_progress,
                show_roi_debug=self.show_roi_debug,
                roi_data=self.roi_data if self.use_roi else None
            )

            # --- 6. RENDEROWANIE KOLEJKI ZDJĘĆ ---
            strip = self.photo_manager.get_thumbnail_strip()
            if strip is not None:
                s_h, s_w = strip.shape[:2]
                x_start = (final_frame.shape[1] - s_w) // 2
                y_start = 35  # Lekko obniżone dla lepszego balansu

                try:
                    roi = final_frame[y_start:y_start + s_h, x_start:x_start + s_w]
                    strip_gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
                    _, mask = cv2.threshold(strip_gray, 5, 255, cv2.THRESH_BINARY)
                    mask_inv = cv2.bitwise_not(mask)

                    bg = cv2.bitwise_and(roi, roi, mask=mask_inv)
                    fg = cv2.bitwise_and(strip, strip, mask=mask)
                    final_frame[y_start:y_start + s_h, x_start:x_start + s_w] = cv2.add(bg, fg)

                    # --- PASEK ŁADOWANIA IDEALNIE POD PIERWSZYM ZDJĘCIEM ---
                    prog = self.photo_manager.get_loading_progress()
                    if 0.0 < prog < 1.0:
                        bar_x = x_start + 15
                        bar_y = y_start + s_h + 12

                        # Szerokość paska = szerokość pierwszego zdjęcia
                        bar_max_w = self.photo_manager.first_thumb_width
                        curr_bar_w = int(bar_max_w * prog)

                        # Rysowanie (Warstwa tła i Warstwa postępu)
                        cv2.rectangle(final_frame, (bar_x, bar_y), (bar_x + bar_max_w, bar_y + 8),
                                      (30, 30, 30), -1)
                        cv2.rectangle(final_frame, (bar_x, bar_y), (bar_x + curr_bar_w, bar_y + 8),
                                        (255, 0, 255), -1)
                        # Biała ramka obrysowująca (cienka)
                        cv2.rectangle(final_frame, (bar_x, bar_y), (bar_x + bar_max_w, bar_y + 8),
                                      (255, 255, 255), 1)
                except Exception:
                    pass

            # --- 7. DEBUG ROI (WERSJA BEZPIECZNA) ---
            states_with_cam = [s for s, cfg in UI_CONFIG.items() if cfg["show_cam"]]
            if self.show_roi_debug and self.use_roi and self.roi_data and (self.cam.frame is not None):
                if self.ui_manager.current_state in states_with_cam:
                    r = self.roi_data
                    cfg = CAM_CONFIG.get(self.ui_manager.current_state, CAM_CONFIG["IDLE"])
                    try:
                        # Pobieramy bezpiecznie wymiary aktualnej klatki
                        h_raw, w_raw = self.cam.frame.shape[:2]

                        # Wymiary okna docelowego w UI
                        w_target, h_target = cfg['w'], cfg['h']

                        # Obliczamy proporcje
                        aspect_raw = w_raw / h_raw
                        aspect_target = w_target / h_target

                        if aspect_raw > aspect_target:
                            w_crop = int(h_raw * aspect_target)
                            offset_x = (w_raw - w_crop) // 2
                            offset_y = 0
                            scale_x = w_target / w_crop
                            scale_y = h_target / h_raw
                        else:
                            h_crop = int(w_raw / aspect_target)
                            offset_x = 0
                            offset_y = (h_raw - h_crop) // 2
                            scale_x = w_target / w_raw
                            scale_y = h_target / h_crop

                        # Pobieranie wartości z pliku konfiguracyjnego
                        roi_x = r.get('roi_x', 0)
                        roi_y = r.get('roi_y', 0)
                        roi_w = r.get('roi_w', 100)
                        roi_h = r.get('roi_h', 100)

                        # Przeliczenie pozycji uwzględniając położenie okna kamery na ekranie (cfg['x'], cfg['y'])
                        rx = int(cfg['x'] + ((roi_x - offset_x) * scale_x))
                        ry = int(cfg['y'] + ((roi_y - offset_y) * scale_y))
                        rw = int(roi_w * scale_x)
                        rh = int(roi_h * scale_y)

                        # Rysowanie fioletowego prostokąta debugowania bezpośrednio na final_frame
                        cv2.rectangle(final_frame, (rx, ry), (rx + rw, ry + rh), (255, 0, 255), 3)

                    except Exception as e:
                        print(f"[DEBUG ROI CRITICAL ERR] Problem z rysowaniem: {e}")
                        pass

            # Wyświetlanie klatki
            cv2.imshow(win_name, final_frame)

            # Klawisze sterujące
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('w'):
                self.show_roi_debug = not self.show_roi_debug
                print(f"[DEBUG] Widoczność ROI: {self.show_roi_debug}")

            # Stabilizacja FPS
            elapsed = time.time() - start_loop
            wait = frame_time - elapsed
            if wait > 0: time.sleep(wait)

if __name__ == '__main__':
    scanner = SmartScanner()
    try:
        scanner.run_local_window()
    finally:
        cv2.destroyAllWindows()
        scanner.cam.stop()