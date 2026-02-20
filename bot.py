import os
import asyncio
import json
import re
import base64
import requests
import io
import struct
import random
import time
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
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
APP_ID = os.environ.get("APP_ID", "karyna-v2-flash")

# Twoja ekipa (Ziomki)
NASI_ZIOMKI = [
    "Gal", "Karol", "Nassar", "Łukasz", "DonMacias", "Polski Ninja", "Oliv", 
    "One Way Ticket", "Bajtkojn", "Tomek", "Mando", "mateusz", "Pdablju", 
    "XDemon", "Michal K", "SHARK", "KrisFX", "Halison", "Wariat95", "Shadows", 
    "andzia", "Marzena", "Kornello", "Tomasz", "DonMakveli", "Lucifer", 
    "Stara Janina", "Matis64", "Kama", "Kicia", "Kociamber Auuu", "KERTH", 
    "Ulalala", "Dorcia", "Kuba", "Damian", "Marshmallow", "KarolCarlos", 
    "PIRATEPpkas Pkas", "Maniek", "HuntFiWariat9501", "Krystiano1993", 
    "Jazda jazda", "Dottie", "Khent"
]

# Inicjalizacja Firebase Firestore
db = None
fb_config_raw = os.environ.get("FIREBASE_CONFIG")
if fb_config_raw:
    try:
        fb_config = json.loads(fb_config_raw)
        if not firebase_admin._apps:
            cred = credentials.Certificate(fb_config)
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("INFO: Firebase podłączone.")
    except Exception as e:
        print(f"BŁĄD Firebase: {e}")

# Głos Karyny (zgodnie z listą dostępnych głosów)
VOICE_NAME = "Despina" 

# =========================
# FUNKCJE POMOCNICZE
# =========================

def save_to_db(chat_id, user, text):
    if not db: return
    try:
        # Path: /artifacts/{appId}/public/data/{collection}
        doc_ref = db.collection('artifacts', APP_ID, 'public', 'data', 'chat_logs').document()
        doc_ref.set({
            'chat_id': str(chat_id),
            'user': user,
            'text': text,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
    except: pass

def get_chat_history(chat_id):
    if not db: return []
    try:
        # Pobieramy stream i filtrujemy w pamięci (Rule 2)
        docs = db.collection('artifacts', APP_ID, 'public', 'data', 'chat_logs').stream()
        all_msgs = []
        for doc in docs:
            d = doc.to_dict()
            if d.get('chat_id') == str(chat_id):
                all_msgs.append(d)
        all_msgs.sort(key=lambda x: (x.get('timestamp').timestamp() if x.get('timestamp') else 0))
        return [f"{m['user']}: {m['text']}" for m in all_msgs[-60:]] # Ostatnie 60
    except: return []

def pcm_to_wav(pcm_data, sample_rate=24000):
    """Konwertuje surowe dane PCM na format WAV."""
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

# =========================
# GŁÓWNA LOGIKA KARYNY
# =========================

async def ask_karyna(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    history = get_chat_history(chat_id)
    history_str = "\n".join(history)
    ziomki_str = ", ".join(NASI_ZIOMKI)

    sys_instruction = (
        "Jesteś Karyną. Pyskata, wyluzowana dziewczyna z polskiego osiedla. "
        "Nie jesteś AI, jesteś żywym człowiekiem. Mówisz szorstko, potocznie, po polsku. "
        "Jeśli czegoś nie wiesz, powiedz szczerze: 'nie wiem kurwa, nie było mnie tam'. "
        "NIGDY nie zmyślaj faktów, których nie ma w historii rozmowy.\n\n"
        f"NASZA EKIPA (ZIOMKI): {ziomki_str}.\n"
        "Analizuj raporty z gier na zdjęciach. Jeśli nasi przegrali, pociesz ich (w swoim stylu). "
        "Jak wygrali, chwal ich. Nie kłam, że wygraliśmy, jeśli widać porażkę.\n\n"
        f"HISTORIA ROZMÓW:\n{history_str}"
    )

    # Używamy stabilnego modelu Gemini 2.0 Flash
    model_id = "gemini-2.0-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={API_KEY}"

    # Przygotowanie zapytania o TEKST i AUDIO jednocześnie (Multimodal)
    contents = [{
        "parts": [{"text": prompt if prompt else "Co tam?"}]
    }]
    if image_b64:
        contents[0]["parts"].append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": sys_instruction}]},
        "generationConfig": {
            "responseModalities": ["TEXT", "AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": { "voiceName": VOICE_NAME }
                }
            }
        }
    }

    # Wywołanie z retry (Exponential Backoff)
    for i in range(5):
        try:
            res = requests.post(url, json=payload, timeout=60)
            if res.status_code == 200:
                data = res.json()
                
                # Wyciąganie tekstu
                ans_text = ""
                audio_base64 = ""
                for part in data['candidates'][0]['content']['parts']:
                    if 'text' in part:
                        ans_text = part['text']
                    if 'inlineData' in part:
                        audio_base64 = part['inlineData']['data']

                # Wysyłanie odpowiedzi
                if ans_text:
                    await update.message.reply_text(ans_text)
                
                if audio_base64:
                    pcm_data = base64.b64decode(audio_base64)
                    wav_data = pcm_to_wav(pcm_data)
                    await update.message.reply_audio(audio=io.BytesIO(wav_data), filename="karyna.wav", title="Karyna")
                
                return # Sukces
            elif res.status_code == 404:
                # Jeśli 2.0-flash nieznaleziony, spróbuj 1.5-flash jako backup
                url = url.replace("gemini-2.0-flash", "gemini-1.5-flash")
            
            time.sleep(2**i)
        except Exception as e:
            print(f"Błąd API: {e}")
            time.sleep(2**i)

    await update.message.reply_text("Kurwa, coś mnie zacięło. Spróbuj później.")

# =========================
# HANDLERY
# =========================

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ID grupy: `{update.effective_chat.id}`\nFirebase: {'✅' if db else '❌'}")

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS: return

    user = msg.from_user.full_name or "Anonim"
    text = msg.text or msg.caption or ""
    image_b64 = None

    if text: save_to_db(update.effective_chat.id, user, text)

    if msg.photo:
        try:
            p = await msg.photo[-1].get_file()
            buf = io.BytesIO()
            await p.download_to_memory(buf)
            image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        except: pass

    # Reaguje TYLKO na słowo "karyna"
    if "karyna" in text.lower():
        await ask_karyna(update, context, text, image_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna 2.0 Online", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("id", get_id))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot Karyna 2.0 wystartował.")
    application.run_polling()

if __name__ == "__main__":
    main()
