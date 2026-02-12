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
APP_ID = os.environ.get("APP_ID", "karyna-v5")
CHANCE_TO_CHIME_IN = 0.15 

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

VOICE_NAME = "Despina"

# =========================
# BAZA DANYCH (Firestore)
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
    except Exception as e:
        print(f"Błąd zapisu DB: {e}")

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
        return [f"{m['user']}: {m['text']}" for m in all_msgs]
    except Exception as e:
        print(f"Błąd odczytu DB: {e}")
        return []

# =========================
# TTS I AUDIO
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

async def generate_karyna_voice(text):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={API_KEY}"
    voice_prompt = f"Powiedz to jako wyluzowana, pyskata dziewczyna z osiedla: {text}"
    
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
# LOGIKA KARYNY
# =========================

async def run_karyna_logic(update: Update, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    await update.get_bot().send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    history_list = get_chat_history(chat_id)
    history_context = "\n".join(history_list)

    sys_instruction = (
        "Jesteś Karyną, żywą dziewczyną z osiedla. Nie jesteś botem ani AI. "
        "Mówisz szorstko, potocznie, po polsku. Przeklinaj rzadko, tylko dla akcentu. "
        "Twoim zadaniem jest komentowanie tego co piszą inni lub odpowiadanie na pytania. "
        "Bądź wyluzowana, czasem złośliwa, ale kumpelska. "
        "NIGDY nie wspominaj o technologii. Jeśli nie znasz odpowiedzi, powiedz 'nie wiem kurwa'.\n"
        "HISTORIA WASZYCH ROZMÓW Z BAZY DANYCH:\n"
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
            voice_wav = await generate_karyna_voice(ans)
            if voice_wav:
                await update.message.reply_audio(audio=io.BytesIO(voice_wav), filename="karyna.wav", title="Karyna")
    except Exception as e:
        print(f"Error: {e}")

# =========================
# HANDLERY
# =========================

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS: return

    user = msg.from_user.full_name or "Anonim"
    text = msg.text or msg.caption or ""
    
    # FIX: Inicjalizacja zmiennej na samym początku
    image_b64 = None

    if text:
        save_to_db(update.effective_chat.id, user, text)

    # Sprawdzanie obrazka
    if msg.photo:
        try:
            p = await msg.photo[-1].get_file()
            buf = io.BytesIO()
            await p.download_to_memory(buf)
            image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        except: pass

    called_by_name = "karyna" in text.lower()
    random_chime = random.random() < CHANCE_TO_CHIME_IN
    
    if called_by_name or random_chime:
        await run_karyna_logic(update, text, image_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna Online", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot Karyna ruszył!")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
