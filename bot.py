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

# --- KONFIGURACJA ŚRODOWISKA ---
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
APP_ID = os.environ.get("APP_ID", "karyna-debug")

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

# Inicjalizacja Firebase z raportowaniem błędów
db = None
fb_error = None
fb_config_raw = os.environ.get("FIREBASE_CONFIG")
if fb_config_raw:
    try:
        fb_config = json.loads(fb_config_raw)
        if not firebase_admin._apps:
            cred = credentials.Certificate(fb_config)
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("DEBUG: Firebase podłączone.")
    except Exception as e:
        fb_error = str(e)
        print(f"DEBUG: Błąd Firebase: {e}")
else:
    fb_error = "Brak zmiennej FIREBASE_CONFIG"

VOICE_NAME = "Despina"

# =========================
# FUNKCJE
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
        print(f"DEBUG: Błąd zapisu: {e}")

def get_chat_history(chat_id):
    if not db: return []
    try:
        # Pobieramy tylko 20 ostatnich, żeby nie muliło bazy przy błędach
        docs = db.collection('artifacts', APP_ID, 'public', 'data', 'chat_logs').limit(20).get()
        all_msgs = []
        for doc in docs:
            d = doc.to_dict()
            if d.get('chat_id') == str(chat_id):
                all_msgs.append(d)
        all_msgs.sort(key=lambda x: (x.get('timestamp').timestamp() if x.get('timestamp') else 0))
        return [f"{m['user']}: {m['text']}" for m in all_msgs]
    except Exception as e:
        print(f"DEBUG: Błąd historii: {e}")
        return [f"SYSTEM: Błąd bazy danych ({e})"]

async def generate_voice(text):
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
# LOGIKA AI
# =========================

async def run_karyna_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    
    # 1. Informacja o starcie
    print(f"DEBUG: Start logiki dla {chat_id}")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    # 2. Sprawdzenie Firebase
    if fb_error and not db:
        await update.message.reply_text(f"❌ Problem z Firebase: {fb_error}")
    
    history_list = get_chat_history(chat_id)
    history_context = "\n".join(history_list)
    ziomki_str = ", ".join(NASI_ZIOMKI)

    sys_instruction = (
        "Jesteś Karyną. Żywa dziewczyna z osiedla. "
        f"TWOI ZIOMKI: {ziomki_str}\n"
        "Mów szorstko, potocznie. Nie przyznawaj się że jesteś botem.\n"
        f"HISTORIA: {history_context}"
    )

    # 3. Wywołanie Gemini
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
    parts = [{"text": prompt if prompt else "Siema"}]
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
            await update.message.reply_text(ans)
            
            voice_wav = await generate_voice(ans)
            if voice_wav:
                await update.message.reply_audio(audio=io.BytesIO(voice_wav), filename="karyna.wav")
            else:
                await update.message.reply_text("🔈 (Błąd generowania głosu, ale tekst wysłałam)")
        else:
            await update.message.reply_text(f"❌ Gemini wywaliło błąd {res.status_code}: {res.text[:100]}")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Wywaliło bota przy Gemini: {str(e)}")

# =========================
# HANDLERY
# =========================

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_fb = "✅ OK" if db else f"❌ BŁĄD ({fb_error})"
    await update.message.reply_text(f"ID grupy: `{update.effective_chat.id}`\nFirebase: {status_fb}")

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    
    # Omijamy grupy spoza listy
    if update.effective_chat.id not in ALLOWED_GROUPS:
        return

    user = msg.from_user.full_name or "Anonim"
    text = msg.text or msg.caption or ""
    image_b64 = None

    # Zapis do bazy
    if text: save_to_db(update.effective_chat.id, user, text)

    # Zdjęcie
    if msg.photo:
        try:
            p = await msg.photo[-1].get_file()
            buf = io.BytesIO()
            await p.download_to_memory(buf)
            image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        except: pass

    if "karyna" in text.lower():
        await run_karyna_logic(update, context, text, image_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna Debug Online", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("id", get_id))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot Karyna wystartował w trybie DEBUG!")
    application.run_polling()

if __name__ == "__main__":
    main()
