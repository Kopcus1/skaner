import os
import shutil

# --- NAPRAWA 1: Wyciszenie "strasznych" logów gRPC przed importami Google ---
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"

import time
from google.cloud import firestore
from google.cloud import storage

# --- DYNAMICZNE WYLICZANIE ŚCIEŻEK WZGLĘDNYCH ---
# BASE_DIR to: C:\Users\WesolaPC_02\Desktop\skaner\downloader
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# SKANER_DIR to folder nadrzędny: C:\Users\WesolaPC_02\Desktop\skaner
SKANER_DIR = os.path.dirname(BASE_DIR)

# --- KONFIGURACJA ---
TARGET_BUCKET_NAME = "skaner"
COLLECTION_NAME = "qr_codes_skaner"
FILE_EXTENSION = ".png"

# Ścieżki mapowane relatywnie do struktury folderów
LOCAL_DOWNLOAD_FOLDER = os.path.join(SKANER_DIR, "display_system", "download")
LOCAL_ARCHIVE_FOLDER = os.path.join(SKANER_DIR, "display_system", "processed")
LOCAL_FULL_CONTENT_ROOT = os.path.join(SKANER_DIR, "full_content")
PROMPT_SOURCE_FILE = os.path.join(SKANER_DIR, "ai-core-witraz", "prompt.txt")

# Konfiguracja Retry
MAX_RETRIES = 5
RETRY_DELAY = 3

try:
    db = firestore.Client()
    storage_client = storage.Client()
except Exception as e:
    print(f"BŁĄD: Nie można połączyć z Google Cloud. {e}")
    exit(1)

os.makedirs(LOCAL_DOWNLOAD_FOLDER, exist_ok=True)


def download_with_retry(blob, local_path, filename):
    for attempt in range(1, MAX_RETRIES + 1):
        if blob.exists():
            blob.download_to_filename(local_path)
            print(f"   [V] SUKCES: Pobrano {filename}")
            return True
        else:
            print(f"   [!] Synchronizacja Storage... (Próba {attempt}/{MAX_RETRIES})")
            time.sleep(RETRY_DELAY)

    print(f"   [X] BŁĄD: Plik {filename} nie pojawił się w Storage mimo sygnału od AI.")
    return False


def check_and_download(doc_id, ready_count):
    bucket = storage_client.bucket(TARGET_BUCKET_NAME)

    for i in range(1, ready_count + 1):
        filename = f"{doc_id}_{i}{FILE_EXTENSION}"
        filename_no_ext = f"{doc_id}_{i}"

        primary_path = os.path.join(LOCAL_DOWNLOAD_FOLDER, filename)
        secondary_path = os.path.join(LOCAL_ARCHIVE_FOLDER, filename)

        # Docelowy podfolder dla tej konkretnej grafiki
        content_folder = os.path.join(LOCAL_FULL_CONTENT_ROOT, filename_no_ext)
        content_final_path = os.path.join(content_folder, filename)
        prompt_target_path = os.path.join(content_folder, "prompt.txt")

        # Sprawdzanie czy plik graficzny już istnieje
        if os.path.exists(primary_path) or os.path.exists(secondary_path) or os.path.exists(content_final_path):
            continue

        print(f"[{doc_id}] Nowy plik wykryty. Rozpoczynam pobieranie i pakowanie...")

        cloud_path = f"output/{doc_id}/{filename}"
        blob = bucket.blob(cloud_path)

        # 1. Pobieranie obrazka do folderu głównego (download)
        success = download_with_retry(blob, primary_path, filename)

        if success:
            try:
                # 2. Tworzenie podfolderu w full_content
                os.makedirs(content_folder, exist_ok=True)

                # 3. Kopiowanie obrazka do podfolderu
                shutil.copy2(primary_path, content_final_path)

                # 4. Kopiowanie pliku prompt.txt do podfolderu
                if os.path.exists(PROMPT_SOURCE_FILE):
                    shutil.copy2(PROMPT_SOURCE_FILE, prompt_target_path)
                    print(f"   [V] PAKIET: Obraz + Prompt zapisane w /{filename_no_ext}/")
                else:
                    print(f"   [!] OSTRZEŻENIE: Brak pliku źródłowego prompt.txt pod ścieżką!")

            except Exception as e:
                print(f"   [X] BŁĄD OPERACJI PLIKOWYCH: {e}")

            time.sleep(1.5)


def on_snapshot(col_snapshot, changes, read_time):
    if not changes:
        return

    for change in changes:
        if change.type.name in ['ADDED', 'MODIFIED']:
            doc = change.document
            data = doc.to_dict()
            doc_id = doc.id

            stained_glass_data = data.get('StainedGlass', {})

            # Logika AI Sync
            raw_ai_count = stained_glass_data.get('LastProcessedScan', 0)

            try:
                ai_ready_count = int(raw_ai_count)
            except (ValueError, TypeError):
                ai_ready_count = 0

            if ai_ready_count > 0:
                check_and_download(doc_id, ai_ready_count)


def main():
    print(f"--- Downloader Wesoła (Mode: AI Sync + PRE-MASK Source) ---")
    print(f"Nasłuchuje pola: StainedGlass.LastProcessedScan")
    print(f"Źródło: {TARGET_BUCKET_NAME}/pre-mask/[UUID]/...")
    print(f"Cel lokalny: {LOCAL_DOWNLOAD_FOLDER}")
    print("-" * 30)

    doc_ref = db.collection(COLLECTION_NAME)
    query_watch = doc_ref.on_snapshot(on_snapshot)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Zatrzymywanie...")
        query_watch.unsubscribe()


if __name__ == "__main__":
    main()