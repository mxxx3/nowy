import os
import asyncio
import json
import base64
import httpx
import io
import struct
import random
import time
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.constants import ChatAction, ParseMode
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
MODELS_TO_TRY = [
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash"
]

API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
APP_ID = os.environ.get("APP_ID", "karyna-v2026-final")

# Twoja stała lista ziomków (dla wiedzy AI)
NASI_ZIOMKI = "Gal, Karol, Nassar, Łukasz, DonMacias, Polski Ninja, Oliv, One Way Ticket, Bajtkojn, Tomek, Mando, mateusz, Pdablju, XDemon, Michal K, SHARK, KrisFX, Halison, Wariat95, Shadows, andzia, Marzena, Kornello, Tomasz, DonMakveli, Lucifer, Stara Janina, Matis64, Kama, Kicia, Kociamber Auuu, KERTH, Ulalala, Dorcia, Kuba, Damian, Marshmallow, KarolCarlos, PIRATEPpkas Pkas, Maniek, HuntFiWariat9501, Krystiano1993, Jazda jazda, Dottie, Khent"

# Inicjalizacja Firebase (RULE 3 - Auth inside)
db = None
fb_config_raw = os.environ.get("FIREBASE_CONFIG")
if fb_config_raw:
    try:
        fb_config = json.loads(fb_config_raw)
        if not firebase_admin._apps:
            cred = credentials.Certificate(fb_config)
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("INFO: Firebase podłączone pomyślnie.")
    except Exception as e:
        print(f"BŁĄD Firebase: {e}")

VOICE_NAME = "Despina"

# =========================
# NARZĘDZIA BAZY (RULE 1 & 2)
# =========================

async def async_save_db(chat_id, user_data, text):
    """Zapisuje wiadomość i członka ekipy."""
    if not db: return
    try:
        # Zapis logu rozmowy (RULE 1 Path)
        doc_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('chat_logs').document()
        doc_ref.set({
            'chat_id': str(chat_id),
            'user': user_data['name'],
            'text': text,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        
        # Zapis członka grupy do @all (RULE 1 Path)
        member_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('members').document(str(user_data['id']))
        member_ref.set({
            'name': user_data['name'],
            'username': user_data['username'],
            'last_seen': firestore.SERVER_TIMESTAMP
        }, merge=True)
    except Exception as e:
        print(f"DB Save Error: {e}")

def get_history(chat_id):
    """Pobiera historię (RULE 2 - Simple query)."""
    if not db: return []
    try:
        docs = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('chat_logs').stream()
        msgs = []
        for d in docs:
            data = d.to_dict()
            if data.get('chat_id') == str(chat_id):
                msgs.append(data)
        # Sortowanie w pamięci RAM (RULE 2)
        msgs.sort(key=lambda x: (x.get('timestamp').timestamp() if x.get('timestamp') else 0))
        return [f"{m['user']}: {m['text']}" for m in msgs[-20:]]
    except: return []

async def get_team_mentions():
    """Tworzy listę linków do wszystkich Ziomków w bazie."""
    if not db: return ""
    try:
        docs = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('members').stream()
        mentions = []
        for d in docs:
            data = d.to_dict()
            name = data.get('name', 'Ziomek')
            uid = d.id
            mentions.append(f"[{name}](tg://user?id={uid})")
        return ", ".join(mentions)
    except: return ""

# =========================
# NARZĘDZIA AUDIO
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

# =========================
# LOGIKA AI (PARALLEL)
# =========================

async def fetch_ai(client, model_name, payload):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    try:
        res = await client.post(url, json=payload, timeout=30.0)
        if res.status_code == 200: return model_name, res.json()
    except: pass
    return model_name, None

async def run_karyna_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    history = get_history(chat_id)
    history_context = "\n".join(history)

    sys_instruction = (
        "Jesteś Karyną. Pyskata, wyluzowana dziewczyna z polskiego osiedla. Nie jesteś botem.\n"
        f"TWOJA EKIPA (ZIOMKI): {NASI_ZIOMKI}.\n"
        "Jeśli uznasz, że sytuacja jest krytyczna (np. nasi ziomkowie przegrali walkę albo jest ważny temat), "
        "możesz oznaczyć wszystkich pisząc w tekście '@all'. "
        "Rób to rzadko, tylko jak trzeba zrobić dym.\n"
        "Mówisz szorstko, potocznie. Jeśli czegoś nie wiesz, mów 'nie wiem kurwa'.\n\n"
        "HISTORIA ROZMÓW:\n" + history_context
    )

    parts = [{"text": prompt if prompt else "Co tam u ziomków?"}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    payload = {
        "contents": [{"parts": parts}],
        "systemInstruction": {"parts": [{"text": sys_instruction}]},
        "generationConfig": {
            "responseModalities": ["TEXT", "AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICE_NAME}}}
        }
    }

    async with httpx.AsyncClient() as client:
        tasks = [fetch_ai(client, m, payload) for m in MODELS_TO_TRY]
        for completed in asyncio.as_completed(tasks):
            model_name, result = await completed
            if result:
                try:
                    c_parts = result['candidates'][0]['content']['parts']
                    ans_text = next((p['text'] for p in c_parts if 'text' in p), "")
                    audio_b64 = next((p['inlineData']['data'] for p in c_parts if 'inlineData' in p), "")

                    if ans_text:
                        # Zamiana @all na linki
                        if "@all" in ans_text:
                            mentions = await get_team_mentions()
                            ans_text = ans_text.replace("@all", mentions if mentions else "ekipa")
                        
                        await update.message.reply_text(
                            f"{ans_text}\n\n⚡️ {model_name}", 
                            parse_mode=ParseMode.MARKDOWN
                        )
                    
                    if audio_b64:
                        wav = pcm_to_wav(base64.b64decode(audio_b64))
                        await update.message.reply_audio(audio=io.BytesIO(wav), filename="karyna.wav")
                    return
                except: continue

# =========================
# HANDLER
# =========================

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS: return

    user_info = {
        'id': msg.from_user.id,
        'name': msg.from_user.full_name or "Ziomek",
        'username': msg.from_user.username or ""
    }
    text = msg.text or msg.caption or ""
    
    # Zapis w tle (RULE 3 - Auth via Admin SDK already initialized)
    if text:
        asyncio.create_task(async_save_db(update.effective_chat.id, user_info, text))

    image_b64 = None
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
def home(): return "Karyna Online", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot Karyna wystartował poprawnie!")
    application.run_polling()

if __name__ == "__main__":
    main()
