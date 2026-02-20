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
MODEL_NAME = "gemini-3-flash-preview"
API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
APP_ID = os.environ.get("APP_ID", "karyna-3f-opti")

# Ziomki (Wiedza stała)
NASI_ZIOMKI = "Gal, Karol, Nassar, Łukasz, DonMacias, Polski Ninja, Oliv, One Way Ticket, Bajtkojn, Tomek, Mando, mateusz, Pdablju, XDemon, Michal K, SHARK, KrisFX, Halison, Wariat95, Shadows, andzia, Marzena, Kornello, Tomasz, DonMakveli, Lucifer, Stara Janina, Matis64, Kama, Kicia, Kociamber Auuu, KERTH, Ulalala, Dorcia, Kuba, Damian, Marshmallow, KarolCarlos, PIRATEPpkas Pkas, Maniek, HuntFiWariat9501, Krystiano1993, Jazda jazda, Dottie, Khent"

# Inicjalizacja Firebase (RULE 1 & 3)
db = None
fb_config_raw = os.environ.get("FIREBASE_CONFIG")
if fb_config_raw:
    try:
        fb_config = json.loads(fb_config_raw)
        if not firebase_admin._apps:
            cred = credentials.Certificate(fb_config)
            firebase_admin.initialize_app(cred)
        db = firestore.client()
    except Exception as e:
        print(f"BŁĄD Firebase: {e}")

VOICE_NAME = "Despina"

# =========================
# NARZĘDZIA BAZY (Zoptymalizowane pod RULE 1)
# =========================

async def async_save_db(chat_id, user_data, text):
    """Zapisuje logi w dedykowanej kolekcji dla grupy."""
    if not db: return
    try:
        # Segregujemy logi po chat_id w nazwie kolekcji, żeby get_history było błyskawiczne
        # Format: /artifacts/{appId}/public/data/logs_{chat_id}
        collection_name = f"logs_{str(chat_id).replace('-', 'm')}"
        doc_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection(collection_name).document()
        doc_ref.set({
            'user': user_data['name'],
            'text': text,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        
        # Zapis członka grupy do listy @all
        member_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('members').document(str(user_data['id']))
        member_ref.set({
            'name': user_data['name'],
            'username': user_data['username'],
            'last_seen': firestore.SERVER_TIMESTAMP
        }, merge=True)
    except: pass

def get_history(chat_id):
    """Pobiera historię tylko dla tej konkretnej grupy."""
    if not db: return []
    try:
        collection_name = f"logs_{str(chat_id).replace('-', 'm')}"
        # Pobieramy tylko z kolekcji tej grupy - RULE 2 (Simple query)
        docs = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection(collection_name).stream()
        msgs = []
        for d in docs:
            data = d.to_dict()
            msgs.append(data)
        
        # Sortowanie i limitowanie w pamięci RAM
        msgs.sort(key=lambda x: (x.get('timestamp').timestamp() if x.get('timestamp') else 0))
        return [f"{m['user']}: {m['text']}" for m in msgs[-20:]] # Ostatnie 20 dla kontekstu
    except: return []

async def get_team_mentions():
    """Tworzy linki do wszystkich zapamiętanych Ziomków."""
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
# AUDIO I AI
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

async def run_karyna_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    # Błyskawiczne pobieranie historii tylko dla TEJ grupy
    history = get_history(chat_id)
    history_context = "\n".join(history)

    sys_instruction = (
        "Jesteś Karyną. Dziewczyna z osiedla, pyskata, ale lojalna wobec ziomków. "
        f"TWOJA EKIPA: {NASI_ZIOMKI}. "
        "Mówisz szorstko, potocznie. Jeśli czegoś nie wiesz, mów 'nie wiem kurwa'. "
        "Analizuj raporty ze zdjęć. Jak nasi przegrali, pociesz ich. Jak wygrali, chwal. "
        "Używaj '@all' tylko gdy sytuacja jest krytyczna.\n\n"
        "HISTORIA OSTATNICH ROZMÓW:\n" + history_context
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={API_KEY}"
    
    parts = [{"text": prompt if prompt else "Co tam u was?"}]
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

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, timeout=60.0)
            if res.status_code == 200:
                data = res.json()
                c_parts = data['candidates'][0]['content']['parts']
                
                ans_text = next((p['text'] for p in c_parts if 'text' in p), "")
                audio_b64 = next((p['inlineData']['data'] for p in c_parts if 'inlineData' in p), "")

                if ans_text:
                    if "@all" in ans_text:
                        mentions = await get_team_mentions()
                        ans_text = ans_text.replace("@all", mentions if mentions else "ekipa")
                    
                    await update.message.reply_text(ans_text, parse_mode=ParseMode.MARKDOWN)
                
                if audio_base64 := audio_b64:
                    wav = pcm_to_wav(base64.b64decode(audio_base64))
                    await update.message.reply_audio(audio=io.BytesIO(wav), filename="karyna.wav", title="Karyna")
            else:
                print(f"Error AI: {res.status_code}")
    except Exception as e:
        print(f"Logic Error: {e}")

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
    
    # Zapis w tle (Firebase) - Teraz asynchronicznie i do własnej kolekcji
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

    # Odpowiada TYLKO na zawołanie
    if "karyna" in text.lower():
        await run_karyna_logic(update, context, text, image_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna 3-Flash Optimized", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot Karyna (Zoptymalizowany) wystartował!")
    application.run_polling()

if __name__ == "__main__":
    main()
