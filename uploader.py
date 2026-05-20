import os
import time
import shutil
import sys
from google.cloud import storage
from google.oauth2 import service_account

# --- KONFIGURACJA ---
BUCKET_NAME = "skaner"  # Twoja nazwa bucketa
FOLDER_PREFIX = "input"  # Folder w buckecie

# --- POPRAWIONA KONFIGURACJA ŚCIEŻEK ---
CURRENT_DIR = os.getcwd()
BASE_DIR = os.path.join(CURRENT_DIR, "content")
# Ta linia musi wskazywać na folder, do którego login.py zapisuje zdjęcia:
INPUT_FOLDER = os.path.join(BASE_DIR, "CROPPED")

# Pozostałe foldery pomocnicze
ARCHIVE_FOLDER = os.path.join(BASE_DIR, "ARCHIVE_UPLOADED")
ERROR_FOLDER = os.path.join(BASE_DIR, "ERROR_UPLOAD")
KEY_PATH = "serviceAccountKey.json"


class CloudUploader:
    def __init__(self):
        self.ensure_dirs()
        self.client = self.authenticate()
        self.bucket = self.connect_bucket()

    def ensure_dirs(self):
        for path in [INPUT_FOLDER, ARCHIVE_FOLDER, ERROR_FOLDER]:
            if not os.path.exists(path):
                os.makedirs(path)

    def authenticate(self):
        if not os.path.exists(KEY_PATH):
            print(f"[CRITICAL] Brak klucza: {KEY_PATH}")
            sys.exit(1)
        try:
            credentials = service_account.Credentials.from_service_account_file(KEY_PATH)
            client = storage.Client(credentials=credentials, project=credentials.project_id)
            return client
        except Exception as e:
            print(f"[AUTH ERROR] {e}")
            sys.exit(1)

    def connect_bucket(self):
        try:
            # Zakładamy, że bucket istnieje (nie sprawdzamy .exists() by uniknąć błędu uprawnień admina)
            bucket = self.client.bucket(BUCKET_NAME)
            print(f"[BUCKET] Polaczono z: {BUCKET_NAME}")
            return bucket
        except Exception as e:
            print(f"[BUCKET ERROR] {e}")
            sys.exit(1)

    def parse_filename(self, filename):
        try:
            # Usuwamy rozszerzenie i dzielimy po '_'
            name_parts = os.path.splitext(filename)[0].split('_')

            if len(name_parts) >= 3:
                uuid = name_parts[0]
                scan_nr = name_parts[1]
                new_id = name_parts[2]
                return uuid, scan_nr, new_id

            return None, None, None
        except Exception as e:
            print(f"[PARSE ERROR] {e}")
            return None, None, None

    def upload_file(self, filename):
        local_path = os.path.join(INPUT_FOLDER, filename)
        f_id, scan_nr, n_id = self.parse_filename(filename)

        if f_id and scan_nr:
            # Budujemy nazwę docelową w chmurze
            target_name = f"{FOLDER_PREFIX}/{f_id}_{scan_nr}_{n_id}.jpg"
            print(f"--> Upload: {filename} jako {target_name}")
        else:
            print(f"[WARN] Niestandardowy format: {filename}")
            target_name = f"{FOLDER_PREFIX}/{filename}"
            f_id, scan_nr, n_id = "unknown", "unknown", "unknown"

        try:
            blob = self.bucket.blob(target_name)
            blob.metadata = {
                "firestore_id": f_id,
                "scan_number": scan_nr,
                "new_id": n_id
            }
            blob.upload_from_filename(local_path)
            print("OK.")
            return True
        except Exception as e:
            print(f"\n[UPLOAD FAIL] {e}")
            return False

    def run_loop(self):
        print("--- CLOUD UPLOADER (RENAMER) START ---")
        print(f"Watch: {INPUT_FOLDER}")
        print(f"Target: gs://{BUCKET_NAME}/{FOLDER_PREFIX}/[UUID]_[NR].jpg")

        while True:
            try:
                files = [f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
                files.sort(key=lambda x: os.path.getmtime(os.path.join(INPUT_FOLDER, x)))

                if not files:
                    time.sleep(1)
                    continue

                for filename in files:
                    src_path = os.path.join(INPUT_FOLDER, filename)
                    time.sleep(0.5)

                    if self.upload_file(filename):
                        shutil.move(src_path, os.path.join(ARCHIVE_FOLDER, filename))
                    else:
                        shutil.move(src_path, os.path.join(ERROR_FOLDER, filename))

            except KeyboardInterrupt:
                print("\nZatrzymano Uploader.")
                break
            except Exception as e:
                print(f"[LOOP ERROR] {e}")
                time.sleep(2)


if __name__ == "__main__":
    app = CloudUploader()
    app.run_loop()