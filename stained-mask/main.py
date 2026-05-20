import functions_framework
import logging
import os
import io
from google.cloud import storage
from google.cloud import firestore
from PIL import Image

# --- KONFIGURACJA ---
SOURCE_FOLDER = "pre-mask"
OUTPUT_FOLDER = "final"
MASK_FILE_NAME = "maska.png"
FIRESTORE_COLLECTION = "qr_codes"

# Init
try:
    storage_client = storage.Client()
    db = firestore.Client()
except Exception as e:
    logging.critical(f"Krytyczny błąd init: {e}")
    storage_client = None
    db = None


# --- GRAFIKA (Bez zmian) ---
def find_cutout_box(image: Image.Image) -> tuple | None:
    if image.mode != 'RGBA': image = image.convert('RGBA')
    alpha = image.getchannel('A')
    pixels = alpha.load()
    w, h = alpha.size
    y_start, y_end = -1, -1
    for y in range(h):
        if pixels[w // 2, y] < 10:
            if y_start == -1: y_start = y
            y_end = y
    if y_start == -1: return None
    x_start, x_end = -1, -1
    scan_y = (y_start + y_end) // 2
    for x in range(w):
        if pixels[x, scan_y] < 10:
            if x_start == -1: x_start = x
            x_end = x
    if x_start == -1: return None
    return (x_start, y_start, x_end + 1, y_end + 1)


def apply_mask_overlay(input_bytes, bucket):
    mask_blob = bucket.blob(MASK_FILE_NAME)
    if not mask_blob.exists(): raise Exception("Brak maska.png")

    mask_img = Image.open(io.BytesIO(mask_blob.download_as_bytes())).convert("RGBA")
    content_img = Image.open(io.BytesIO(input_bytes)).convert("RGBA")

    box = find_cutout_box(mask_img) or (0, 0, mask_img.width, mask_img.height)
    x, y, x2, y2 = box
    w, h = x2 - x, y2 - y

    OVERLAP = 3
    target_dim = max(w + 2 * OVERLAP, h + 2 * OVERLAP)
    resized = content_img.resize((target_dim, target_dim), Image.Resampling.LANCZOS)

    final = Image.new("RGBA", mask_img.size)
    final.paste(resized, (x - OVERLAP, y - OVERLAP), resized)
    final.paste(mask_img, (0, 0), mask_img)

    buf = io.BytesIO()
    final.save(buf, format="PNG")
    return buf.getvalue()


# --- GŁÓWNA FUNKCJA ---
@functions_framework.cloud_event
def apply_mask(cloud_event):
    logging.basicConfig(level=logging.INFO)
    data = cloud_event.data
    bucket_name = data.get("bucket")
    file_name = data.get("name")

    # 1. Sprawdzenie czy to folder pre-mask
    if not file_name or not file_name.startswith(f"{SOURCE_FOLDER}/"):
        return  # To nie nasz folder, ignorujemy cicho

    logging.info(f"PROCESSING: Wykryto plik: {file_name}")

    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)

        # Parsowanie ścieżki: pre-mask / UUID / nazwa_pliku.png
        parts = file_name.split('/')
        if len(parts) < 3:
            logging.warning(f"Zła struktura folderów: {file_name}")
            return

        qr_uuid = parts[1]
        original_name = parts[-1]

        # Przetwarzanie
        final_bytes = apply_mask_overlay(blob.download_as_bytes(), bucket)

        # Nazwa wyjściowa: final / UUID / nazwa_pliku_final.png
        clean_name = os.path.splitext(original_name)[0]
        # Usuwamy śmieci z nazwy jeśli są, ale nie wymagamy ich
        clean_name = clean_name.replace("_lineart", "").replace("_no_background", "")

        new_path = f"{OUTPUT_FOLDER}/{qr_uuid}/{clean_name}_final.png"

        # Upload
        bucket.blob(new_path).upload_from_string(final_bytes, content_type="image/png")
        logging.info(f"UPLOAD SUKCES: {new_path}")

        # Firestore Update
        if qr_uuid != "unknown":
            doc_ref = db.collection(FIRESTORE_COLLECTION).document(qr_uuid)
            update_data = {
                'Mask.IsMask': True,
                'StainedGlass.status': 'Gotowe',
                'StainedGlass.LastUpdate': firestore.SERVER_TIMESTAMP
            }
            try:
                doc_ref.update(update_data)
                logging.info(f"FIRESTORE: Zaktualizowano {qr_uuid}")
            except Exception:
                # Fallback jeśli dokument nie istnieje
                doc_ref.set(update_data, merge=True)
                logging.info(f"FIRESTORE: Utworzono wpis {qr_uuid}")

    except Exception as e:
        logging.error(f"BLAD: {e}")