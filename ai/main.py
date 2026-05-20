import os
import time
import logging
import functions_framework
from google import genai
from google.genai import types
from google.cloud import storage
from google.cloud import firestore

# --- KONFIGURACJA ---
PROJECT_ID = os.environ.get("GCP_PROJECT")
COLLECTION_NAME = "qr_codes_skaner"  # Zmieniono na Twoją kolekcję skanera
MODEL_NAME = "gemini-3.1-flash-image-preview"

# --- INICJALIZACJA KLIENTÓW ---
try:
    storage_client = storage.Client()
    db = firestore.Client(project=PROJECT_ID)
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        logging.critical("Brak GOOGLE_API_KEY!")
        ai_client = None
    else:
        ai_client = genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})
        logging.info("AI Client zainicjalizowany.")
except Exception as e:
    logging.critical(f"Błąd inicjalizacji: {e}")
    ai_client = None


def get_prompt_by_custom_id(custom_id):
    """Wczytuje prompt na podstawie CustomID (np. prompt_12.txt)"""
    try:
        specific_prompt = f'prompt_{custom_id}.txt'
        prompt_path = os.path.join(os.path.dirname(__file__), specific_prompt)

        if not os.path.exists(prompt_path):
            logging.warning(f"Brak promptu dla CustomID {custom_id}. Szukam prompt.txt")
            prompt_path = os.path.join(os.path.dirname(__file__), 'prompt.txt')

        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        logging.error(f"BŁĄD ODCZYTU PROMPTU: {e}")
        return "Transform the sketch into a stained glass art piece, preserving geometry."


@functions_framework.cloud_event
def process_storage_image(cloud_event):
    data = cloud_event.data
    bucket_name = data["bucket"]
    file_name = data["name"]

    logging.info(f"--- START PROCESU: {file_name} ---")

    if not file_name.startswith("input/"):
        return

    doc_uuid = None
    custom_id = "default"
    scan_nr = 1
    result_bytes = None

    try:
        # 1. PARSOWANIE
        base_name = os.path.basename(file_name)
        name_only = os.path.splitext(base_name)[0]
        parts = name_only.split('_')

        if len(parts) >= 3:
            doc_uuid = parts[0]
            scan_nr = parts[1]
            custom_id = parts[2]
        else:
            logging.error(f"Błędny format nazwy pliku: {base_name}")
            return

        # 2. STATUS: PROCESSING
        db.collection(COLLECTION_NAME).document(doc_uuid).set({
            "StainedGlass": {
                "status": "processing",
                "custom_id": custom_id,
                "current_nr": scan_nr,
                "LastUpdate": firestore.SERVER_TIMESTAMP
            }
        }, merge=True)

        # 3. POBRANIE OBRAZU
        bucket = storage_client.bucket(bucket_name)
        image_bytes = bucket.blob(file_name).download_as_bytes()
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        current_prompt = get_prompt_by_custom_id(custom_id)

        # 4. KONFIGURACJA
        config = {
            "response_modalities": ["IMAGE"],
            "candidate_count": 1,
            "safety_settings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
            ]
        }

        # 5. PĘTLA RETRY
        for attempt in range(1, 5): # 4 próby
            try:
                logging.info(f"Próba {attempt} dla {doc_uuid}...")
                response = ai_client.models.generate_content(
                    model=MODEL_NAME,
                    contents=[current_prompt, image_part],
                    config=config
                )

                if response.candidates and response.candidates[0].content.parts:
                    candidate = response.candidates[0]
                    for part in candidate.content.parts:
                        if hasattr(part, 'inline_data') and part.inline_data:
                            result_bytes = part.inline_data.data
                            break
                        elif hasattr(part, 'image') and part.image:
                            result_bytes = getattr(part.image, 'image_bytes', part.image)
                            break

                if result_bytes:
                    logging.info("Otrzymano obraz pomyślnie.")
                    break

                raise Exception("AI zwróciło pusta odpowiedź.")

            except Exception as e:
                logging.warning(f"Błąd w próbie {attempt}: {e}")
                if attempt < 4:
                    time.sleep(attempt * 3)
                else:
                    raise e

        # --- SEKCJA 6: TERAZ JEST POPRAWNIE POZA PĘTLĄ FOR ---
                # --- SEKCJA 6: NOWA LOGIKA NAZW PLIKÓW WYJŚCIOWYCH ---
        if result_bytes:
                    # Zmiana nazwy na format: [ID]_[nr_skanu].png
            output_path = f"output/{doc_uuid}/{doc_uuid}_{scan_nr}.png"
            bucket.blob(output_path).upload_from_string(result_bytes, content_type="image/png")

                    # Powiadomienie bazy danych
            db.collection(COLLECTION_NAME).document(doc_uuid).set({
                "StainedGlass": {
                    "status": "ready",
                    "LastProcessedScan": scan_nr,
                    "result_url": output_path,
                    "LastUpdate": firestore.SERVER_TIMESTAMP
                }
            }, merge=True)
            logging.info(
                f"SUKCES: Status READY wysłany do Firestore dla {doc_uuid}. Nazwa pliku: {doc_uuid}_{scan_nr}.png")
        else:
            raise Exception("Brak danych obrazu po zakończeniu prób.")

    except Exception as e:
        logging.error(f"KRYTYCZNY BŁĄD AI: {e}")
        if doc_uuid:
            db.collection(COLLECTION_NAME).document(doc_uuid).set({
                "StainedGlass": {
                    "status": "error",
                    "error_msg": str(e),
                    "LastUpdate": firestore.SERVER_TIMESTAMP
                }
            }, merge=True)

