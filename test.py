import cv2
import json
import os

# --- KONFIGURACJA ---
CAMERA_INDEX = 0
CONFIG_FILE = "roi_config.json"

FRAME_WIDTH = 2400
FRAME_HEIGHT = 1792

TARGET_W = 777
TARGET_H = 589

# Zmienne globalne w układzie współrzędnych WYŚWIETLANEGO OKNA (0 do TARGET_W/H)
drawing = False
ix, iy = -1, -1
ex, ey = -1, -1
roi_display_coords = None


def mouse_callback(event, x, y, flags, param):
    global ix, iy, ex, ey, drawing, roi_display_coords

    # Zabezpieczenie przed wyjściem myszy poza obszar wirtualnego kadru
    x = max(0, min(TARGET_W, x))
    y = max(0, min(TARGET_H, y))

    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        ix, iy = x, y
        ex, ey = x, y

    elif event == cv2.EVENT_MOUSEMOVE:
        if drawing:
            ex, ey = x, y

    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        ex, ey = x, y
        x1, y1 = min(ix, ex), min(iy, ey)
        x2, y2 = max(ix, ex), max(iy, ey)
        roi_display_coords = (x1, y1, x2, y2)


def save_config(coords, offset_x, offset_y, scale_x, scale_y):
    if coords is None:
        print("[ERR] Nie zaznaczono obszaru! Nie zapisano pliku.")
        return

    dx1, dy1, dx2, dy2 = coords

    # DOPIERO TUTAJ przeliczamy współrzędne z okna na surową klatkę 2400x1792
    real_x1 = int((dx1 / scale_x) + offset_x)
    real_y1 = int((dy1 / scale_y) + offset_y)
    real_w = int((dx2 - dx1) / scale_x)
    real_h = int((dy2 - dy1) / scale_y)

    data = {
        "camera_index": CAMERA_INDEX,
        "original_frame_w": FRAME_WIDTH,
        "original_frame_h": FRAME_HEIGHT,
        "roi_x": max(0, real_x1),
        "roi_y": max(0, real_y1),
        "roi_w": min(FRAME_WIDTH, real_w),
        "roi_h": min(FRAME_HEIGHT, real_h)
    }

    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"\n=========================================")
        print(f"[OK] Konfiguracja zapisana w: {CONFIG_FILE}")
        print(f"Współrzędne dla głównego skryptu:")
        print(f"X: {data['roi_x']}, Y: {data['roi_y']}")
        print(f"W: {data['roi_w']}, H: {data['roi_h']}")
        print(f"=========================================")
    except Exception as e:
        print(f"[ERR] Błąd zapisu pliku: {e}")


def run_calibration():
    global roi_display_coords, drawing, ix, iy, ex, ey

    print("\n--- Narzędzie do kalibracji obszaru skanowania (ROI) ---")
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)

    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    if not cap.isOpened():
        print(f"[ERR] Nie można otworzyć kamery o indeksie {CAMERA_INDEX}")
        return

    win_name = "Kalibracja ROI - System Wesola"

    # KLUCZOWA ZMIANA: Blokujemy rozmiar okna na stałe 777x589 podczas rysowania,
    # dzięki czemu 1 piksel myszy == 1 piksel na ekranie. Brak rozjeżdżania się!
    cv2.namedWindow(win_name, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(win_name, mouse_callback)

    print("\nINSTRUKCJA:")
    print("1. Narysuj prostokąt myszką na podglądzie (będzie idealnie podążać za kursorem).")
    print("2. Wciśnij 's', aby ZAPISAĆ i wyjść.")
    print("3. Wciśnij 'q' lub Esc, aby WYJŚĆ BEZ ZAPISU.")

    # Inicjalizacja zmiennych transformacji
    offset_x, offset_y = 0, 0
    scale_x, scale_y = 1.0, 1.0

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERR] Błąd odczytu z kamery.")
            break

        h_raw, w_raw = frame.shape[:2]
        aspect_raw = w_raw / h_raw
        aspect_target = TARGET_W / TARGET_H

        # Center Crop (dokładnie to co robi kiosk)
        if aspect_raw > aspect_target:
            new_w = int(h_raw * aspect_target)
            offset_x = (w_raw - new_w) // 2
            offset_y = 0
            cropped_cam = frame[:, offset_x:offset_x + new_w]
        else:
            new_h = int(w_raw / aspect_target)
            offset_x = 0
            offset_y = (h_raw - new_h) // 2
            cropped_cam = frame[offset_y:offset_y + new_h, :]

        # Tworzymy klatkę do wyświetlenia na ekranie
        display_frame = cv2.resize(cropped_cam, (TARGET_W, TARGET_H))

        # Zapamiętujemy parametry skali do późniejszego zapisu pliku JSON
        h_crop, w_crop = cropped_cam.shape[:2]
        scale_x = TARGET_W / w_crop
        scale_y = TARGET_H / h_crop

        # Rysowanie prostokąta w czasie rzeczywistym (współrzędne okna ekranu)
        if drawing:
            cv2.rectangle(display_frame, (ix, iy), (ex, ey), (0, 255, 0), 2)
        elif roi_display_coords:
            x1, y1, x2, y2 = roi_display_coords
            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.putText(display_frame, "OBSZAR SKANOWANIA", (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)

        # Celownik pomocniczy
        cx, cy = TARGET_W // 2, TARGET_H // 2
        cv2.line(display_frame, (cx - 15, cy), (cx + 15, cy), (0, 0, 255), 1)
        cv2.line(display_frame, (cx, cy - 15), (cx, cy + 15), (0, 0, 255), 1)

        cv2.imshow(win_name, display_frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('s'):
            save_config(roi_display_coords, offset_x, offset_y, scale_x, scale_y)
            break
        elif key == ord('q') or key == 27:
            print("[INFO] Wyjście bez zapisywania.")
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    run_calibration()