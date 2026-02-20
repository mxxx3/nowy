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

# --- KONFIGURACJA ŚRODOWISKA ---
import telegram.ext
class DummyJobQueue:
    def __init__(self, *args, **kwargs): pass
    def set_application(self, application): pass
    async def start(self): pass
    async def stop(self): pass
telegram.ext.JobQueue = DummyJobQueue

# =========================
# KONFIGURACJA I LISTA MODELI
# =========================
# Bot będzie próbował tych modeli po kolei:
MODELS_TO_TRY = [
    "gemini-2.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.0-flash",
    "gemini-1.5-flash"
]

API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
APP_ID = os.environ.get("APP_ID", "karyna-multi-v1")

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

# Inicjalizacja Firebase
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

VOICE_NAME = "Despina"

# =========================
# NARZĘDZIA
# =========================

def save_to_db(chat_id, user, text):
    if not db: return
    try:
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
        docs = db.collection('artifacts', APP_ID, 'public', 'data', 'chat_logs').stream()
        all_msgs = []
        for doc in docs:
            d = doc.to_dict()
            if d.get('chat_id') == str(chat_id):
                all_msgs.append(d)
        all_msgs.sort(key=lambda x: (x.get('timestamp').timestamp() if x.get('timestamp') else 0))
        return [f"{m['user']}: {m['text']}" for m in all_msgs[-50:]]
    except: return []

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

# =========================
# LOGIKA KARYNY (Z FALLBACKIEM)
# =========================

async def run_karyna(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    history = get_chat_history(chat_id)
    history_str = "\n".join(history)
    ziomki_str = ", ".join(NASI_ZIOMKI)

    sys_prompt = (
        "Jesteś Karyną. Pyskata, wyluzowana dziewczyna z polskiego osiedla. "
        "Nie jesteś AI, tylko człowiekiem. Piszesz szorstko, potocznie, po polsku. "
        "Jeśli czegoś nie wiesz, mów szczerze: 'nie wiem kurwa'. "
        "Twoja ekipa to: " + ziomki_str + ". "
        "Gdy widzisz raporty z gier na zdjęciach, bądź lojalna wobec swojej ekipy. "
        "Nie zmyślaj informacji, jeśli nie masz na nie dowodów w historii rozmowy.\n\n"
        "OSTATNIE ROZMOWY:\n" + history_str
    )

    parts = [{"text": prompt if prompt else "Siema, co tam u ziomków?"}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    payload = {
        "contents": [{"parts": parts}],
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

    # PĘTLA TESTOWANIA MODELI (Fallback)
    success = False
    last_error = ""
    
    for model_name in MODELS_TO_TRY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
        try:
            print(f"DEBUG: Próba wywołania modelu: {model_name}")
            res = requests.post(url, json=payload, timeout=60)
            
            if res.status_code == 200:
                data = res.json()
                candidate_parts = data['candidates'][0]['content']['parts']
                
                ans_text = ""
                audio_b64 = ""
                
                for part in candidate_parts:
                    if 'text' in part:
                        ans_text = part['text']
                    if 'inlineData' in part:
                        audio_b64 = part['inlineData']['data']

                if ans_text:
                    await update.message.reply_text(ans_text)
                
                if audio_b64:
                    wav_data = pcm_to_wav(base64.b64decode(audio_b64))
                    await update.message.reply_audio(audio=io.BytesIO(wav_data), filename="karyna.wav", title=f"Karyna ({model_name})")
                
                success = True
                print(f"INFO: Sukces z modelem {model_name}")
                break # Przerwij pętlę po sukcesie
            else:
                last_error = f"{model_name} (Error {res.status_code})"
                print(f"DEBUG: {model_name} nie zadziałał: {res.status_code}")
        except Exception as e:
            last_error = f"{model_name} (Exception: {str(e)})"
            print(f"DEBUG: Wyjątek dla {model_name}: {e}")
            continue

    if not success:
        await update.message.reply_text(f"❌ Wszystkie modele padły. Ostatni błąd: {last_error}")

# =========================
# HANDLERY
# =========================

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    models_status = "\n".join([f"- {m}" for m in MODELS_TO_TRY])
    await update.message.reply_text(f"ID grupy: `{update.effective_chat.id}`\nModele w kolejce:\n{models_status}\nFirebase: {'✅' if db else '❌'}")

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

    if "karyna" in text.lower():
        await run_karyna(update, context, text, image_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna Multi-Model Online", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("id", get_id))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot Karyna z systemem fallback wystartował.")
    application.run_polling()

if __name__ == "__main__":
    main()
