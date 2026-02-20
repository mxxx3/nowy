import os
import asyncio
import json
import re
import base64
import requests
import io
import struct
import random
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
# WAŻNE: Sprawdź to ID komendą /id po uruchomieniu!
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
APP_ID = os.environ.get("APP_ID", "karyna-final-v1")

# LISTA NASZYCH LUDZI (ZIOMKI)
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

async def generate_voice(text):
    # Fix dla 404: Użycie poprawnego modelu TTS
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": { "voiceConfig": { "prebuiltVoiceConfig": { "voiceName": VOICE_NAME } } }
        }
    }
    try:
        res = requests.post(url, json=payload, timeout=20)
        if res.status_code == 200:
            audio_data = res.json()['candidates'][0]['content']['parts'][0]['inlineData']
            pcm_bytes = base64.b64decode(audio_data['data'])
            with io.BytesIO() as wav_buf:
                wav_buf.write(b'RIFF')
                wav_buf.write(struct.pack('<I', 36 + len(pcm_bytes)))
                wav_buf.write(b'WAVEfmt ')
                wav_buf.write(struct.pack('<I', 16))
                wav_buf.write(struct.pack('<HHIIHH', 1, 1, 24000, 48000, 2, 16))
                wav_buf.write(b'data')
                wav_buf.write(struct.pack('<I', len(pcm_bytes)))
                wav_buf.write(pcm_bytes)
                return wav_buf.getvalue()
    except: return None

# =========================
# LOGIKA KARYNY
# =========================

async def run_karyna_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    # FIX: Użycie context.bot zamiast update.get_bot()
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    history_context = "\n".join(get_chat_history(chat_id))
    ziomki_str = ", ".join(NASI_ZIOMKI)

    sys_instruction = (
        "Jesteś Karyną. Żywa dziewczyna z osiedla. Nie jesteś botem ani AI. "
        "Mówisz szorstko, potocznie, po polsku. Przeklinaj rzadko.\n"
        f"TWOI ZIOMKI (NASZA EKIPA): {ziomki_str}\n"
        "Analizuj raporty z gry. Jeśli nasi przegrali, pociesz ich. Jak wygrali, chwal ich.\n"
        "Jeśli nie znasz odpowiedzi, powiedz 'nie wiem kurwa'.\n"
        f"HISTORIA CZATU:\n{history_context}"
    )

    # Fix dla 404: Poprawny model Gemini
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
    parts = [{"text": prompt if prompt else "Co tam?"}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    try:
        payload = {
            "contents": [{"parts": parts}],
            "systemInstruction": {"parts": [{"text": sys_instruction}]}
        }
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            ans = res.json()['candidates'][0]['content']['parts'][0]['text']
            # Wysyłamy tekst i głos
            await update.message.reply_text(ans)
            voice_wav = await generate_voice(ans)
            if voice_wav:
                await update.message.reply_audio(audio=io.BytesIO(voice_wav), filename="karyna.wav")
        else:
            print(f"Błąd Gemini: {res.status_code}")
    except Exception as e:
        print(f"Błąd logiczny: {e}")

# =========================
# HANDLERY
# =========================

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Komenda do sprawdzenia ID grupy."""
    await update.message.reply_text(f"ID tej grupy to: {update.effective_chat.id}")

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    
    # DEBUG: Jeśli Karyna milczy, sprawdź czy ID grupy jest na liście ALLOWED_GROUPS
    if update.effective_chat.id not in ALLOWED_GROUPS:
        return

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

    # Odpowiada TYLKO jeśli wywołano imię "karyna"
    if "karyna" in text.lower():
        await run_karyna_logic(update, context, text, image_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna Live", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("id", get_id))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot Karyna wystartował!")
    application.run_polling()

if __name__ == "__main__":
    main()
