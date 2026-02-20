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
# KONFIGURACJA MODELI 2026
# =========================
TEXT_MODEL = "gemini-2.5-flash-lite"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
APP_ID = os.environ.get("APP_ID", "karyna-2026-v1")

# Lista Ziomków
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
# FUNKCJE POMOCNICZE
# =========================

def save_to_db(chat_id, user, text):
    if not db: return
    try:
        # Path: /artifacts/{appId}/public/data/chat_logs
        doc_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('chat_logs').document()
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
        docs = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('chat_logs').stream()
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
# GENEROWANIE GŁOSU (TTS)
# =========================

async def get_voice_audio(text):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent?key={API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": { "voiceName": VOICE_NAME }
                }
            }
        }
    }
    
    for i in range(3):
        try:
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                audio_b64 = res.json()['candidates'][0]['content']['parts'][0]['inlineData']['data']
                return pcm_to_wav(base64.b64decode(audio_b64))
        except:
            time.sleep(1)
    return None

# =========================
# LOGIKA KARYNY
# =========================

async def run_karyna(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    history = get_chat_history(chat_id)
    history_str = "\n".join(history)
    ziomki_str = ", ".join(NASI_ZIOMKI)

    sys_prompt = (
        "Jesteś Karyną. Żywa dziewczyna z polskiego osiedla, pyskata, wyluzowana. "
        "Nie jesteś AI, tylko człowiekiem. Piszesz szorstko, potocznie. "
        "Jeśli czegoś nie wiesz, mów szczerze: 'nie wiem kurwa'. "
        "Twoja ekipa to: " + ziomki_str + ". "
        "Gdy widzisz raporty z gry na zdjęciach, bądź lojalna wobec swojej ekipy. "
        "Gdy nasi przegrali, pociesz ich w swoim stylu, gdy wygrali - chwal.\n\n"
        "OSTATNIE ROZMOWY:\n" + history_str
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{TEXT_MODEL}:generateContent?key={API_KEY}"
    
    parts = [{"text": prompt if prompt else "Siema, co tam?"}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    payload = {
        "contents": [{"parts": parts}],
        "systemInstruction": {"parts": [{"text": sys_prompt}]}
    }

    try:
        res = requests.post(url, json=payload, timeout=40)
        if res.status_code == 200:
            ans_text = res.json()['candidates'][0]['content']['parts'][0]['text']
            
            # Najpierw tekst
            await update.message.reply_text(ans_text)
            
            # Potem audio
            audio_data = await get_voice_audio(ans_text)
            if audio_data:
                await update.message.reply_audio(audio=io.BytesIO(audio_data), filename="karyna.wav", title="Karyna")
        else:
            await update.message.reply_text(f"❌ Gemini Error {res.status_code}. Coś mnie przycięło.")
    except Exception as e:
        await update.message.reply_text(f"❌ Wywaliło bota: {str(e)}")

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

    # Reaguje TYLKO na zawołanie
    if "karyna" in text.lower():
        await run_karyna(update, context, text, image_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna 2026 Online", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("id", get_id))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot Karyna 2026 ruszył!")
    application.run_polling()

if __name__ == "__main__":
    main()
