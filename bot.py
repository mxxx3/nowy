import os
import asyncio
import json
import re
import base64
import requests
import io
import struct
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)
import firebase_admin
from firebase_admin import credentials, firestore

# --- KONFIGURACJA ŚRODOWISKA (Koyeb Fix) ---
import telegram.ext
class DummyJobQueue:
    def __init__(self, *args, **kwargs): pass
    def set_application(self, application): pass
    async def start(self): pass
    async def stop(self): pass
telegram.ext.JobQueue = DummyJobQueue

# =========================
# KONFIGURACJA
# =========================
API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
APP_ID = os.environ.get("APP_ID", "karyna-v3")

# Inicjalizacja Firebase Firestore
fb_config_raw = os.environ.get("FIREBASE_CONFIG")
if fb_config_raw:
    try:
        fb_config = json.loads(fb_config_raw)
        cred = credentials.Certificate(fb_config)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("INFO: Firebase podłączone.")
    except Exception as e:
        print(f"BŁĄD Firebase: {e}")
        db = None
else:
    db = None

VOICE_NAME = "Despina" # Karyna

# =========================
# OBSŁUGA BAZY DANYCH (Firestore)
# =========================

def save_to_db(chat_id, user, text):
    """Zapisuje każdą wiadomość do stałej historii."""
    if not db: return
    try:
        doc_ref = db.collection('artifacts', APP_ID, 'public', 'data', 'chat_logs').document()
        doc_ref.set({
            'chat_id': str(chat_id),
            'user': user,
            'text': text,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"Błąd zapisu: {e}")

def get_full_history(chat_id):
    """Pobiera CAŁĄ historię czatu dla danego chat_id."""
    if not db: return []
    try:
        # Pobieramy wszystkie dokumenty z kolekcji
        docs = db.collection('artifacts', APP_ID, 'public', 'data', 'chat_logs').stream()
        
        all_msgs = []
        for doc in docs:
            d = doc.to_dict()
            if d.get('chat_id') == str(chat_id):
                all_msgs.append(d)
        
        # Sortowanie po czasie (jeśli jest timestamp)
        all_msgs.sort(key=lambda x: (x.get('timestamp').timestamp() if x.get('timestamp') else 0))
        
        return [f"{m['user']}: {m['text']}" for m in all_msgs]
    except Exception as e:
        print(f"Błąd odczytu bazy: {e}")
        return []

# =========================
# GŁOS I TTS
# =========================

def pcm_to_wav(pcm_data, sample_rate=24000):
    num_channels = 1
    sample_width = 2
    with io.BytesIO() as wav_buf:
        wav_buf.write(b'RIFF')
        wav_buf.write(struct.pack('<I', 36 + len(pcm_data)))
        wav_buf.write(b'WAVEfmt ')
        wav_buf.write(struct.pack('<I', 16))
        wav_buf.write(struct.pack('<HHIIHH', 1, num_channels, sample_rate, sample_rate * num_channels * sample_width, num_channels * sample_width, sample_width * 8))
        wav_buf.write(b'data')
        wav_buf.write(struct.pack('<I', len(pcm_data)))
        wav_buf.write(pcm_data)
        return wav_buf.getvalue()

async def generate_voice(text):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={API_KEY}"
    # Styl Karyny wymuszony w samym generatorze głosu
    voice_prompt = f"Powiedz to jako pyskata, wyluzowana dziewczyna z osiedla: {text}"
    
    payload = {
        "contents": [{"parts": [{"text": voice_prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": { "voiceConfig": { "prebuiltVoiceConfig": { "voiceName": VOICE_NAME } } }
        }
    }
    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            audio_data = res.json()['candidates'][0]['content']['parts'][0]['inlineData']
            pcm_bytes = base64.b64decode(audio_data['data'])
            return pcm_to_wav(pcm_bytes, 24000)
    except: return None

# =========================
# CORE LOGIC
# =========================

async def ask_karyna(update: Update, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    
    # Pobieramy historię bez limitów (całość z bazy)
    full_history = get_full_history(chat_id)
    history_context = "\n".join(full_history)

    sys_instruction = (
        "Jesteś Karyną. Żywym człowiekiem, dziewczyną z osiedla. "
        "Masz zakaz mówienia, że jesteś AI, botem czy programem. Nie znasz się na algorytmach. "
        "Mówisz szorstko, po polsku, wyluzowanie. Używasz potocznego języka. "
        "Jeśli czegoś nie wiesz, mówisz szczerze: 'nie wiem kurwa, nie było mnie przy tym'. "
        "Twoim źródłem wiedzy o tym co się dzieje jest poniższa historia rozmów.\n\n"
        "CAŁA HISTORIA ROZMÓW Z GRUPY:\n"
        f"{history_context}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
    parts = [{"text": prompt}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    try:
        payload = {
            "contents": [{"parts": parts}],
            "systemInstruction": {"parts": [{"text": sys_instruction}]}
        }
        res = requests.post(url, json=payload, timeout=60)
        if res.status_code == 200:
            ans = res.json()['candidates'][0]['content']['parts'][0]['text']
            # Wyślij tymczasowy tekst
            temp_msg = await update.message.reply_text(ans)
            
            # Generuj audio
            voice_wav = await generate_voice(ans)
            if voice_wav:
                await update.message.reply_audio(
                    audio=io.BytesIO(voice_wav),
                    filename="karyna.wav",
                    title="Karyna"
                )
                # Usuń tekst po wysłaniu audio
                try: await temp_msg.delete()
                except: pass
    except:
        await update.message.reply_text("Kurwa, coś mnie zacięło.")

# =========================
# HANDLERY
# =========================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS: return

    user = msg.from_user.full_name or "Anonim"
    text = msg.text or msg.caption or ""

    # Zawsze zapisuj do bazy (budowanie historii)
    if text:
        save_to_db(update.effective_chat.id, user, text)

    # Sprawdzanie czy wywołano Karynę (słowo w tekście)
    if "karyna" in text.lower():
        # Przygotowanie obrazka
        img_b64 = None
        if msg.photo:
            p = await msg.photo[-1].get_file()
            buf = io.BytesIO()
            await p.download_to_memory(buf)
            img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        
        await ask_karyna(update, text, img_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna is Watching", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    print("Bot Karyna wystartował!")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
