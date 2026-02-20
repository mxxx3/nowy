import os
import asyncio
import json
import base64
import httpx
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

# Globalna pamięć bota (ładowana przy starcie)
HISTORY_CACHE = {} # {chat_id: [messages]}
MEMBERS_CACHE = {} # {chat_id: {user_id: name}}

NASI_ZIOMKI = "Gal, Karol, Nassar, Łukasz, DonMacias, Polski Ninja, Oliv, One Way Ticket, Bajtkojn, Tomek, Mando, mateusz, Pdablju, XDemon, Michal K, SHARK, KrisFX, Halison, Wariat95, Shadows, andzia, Marzena, Kornello, Tomasz, DonMakveli, Lucifer, Stara Janina, Matis64, Kama, Kicia, Kociamber Auuu, KERTH, Ulalala, Dorcia, Kuba, Damian, Marshmallow, KarolCarlos, PIRATEPpkas Pkas, Maniek, HuntFiWariat9501, Krystiano1993, Jazda jazda, Dottie, Khent"

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
        print("[INFO] Firebase podłączone pomyślnie.")
    except Exception as e:
        print(f"[ERROR] Błąd inicjalizacji Firebase: {e}")

# =========================
# SYSTEM PAMIĘCI (RAM)
# =========================

def load_all_data_to_ram():
    """Pobiera całą dostępną historię z Firebase przy starcie bota."""
    if not db:
        print("[WARNING] Brak dostępu do DB. Startuję bez historii.")
        return

    print("[DEBUG] Rozpoczynam pobieranie historii z Firebase do RAM...")
    for chat_id in ALLOWED_GROUPS:
        try:
            chat_id_str = str(chat_id)
            # Pobieramy logi z dedykowanej kolekcji grupy
            coll_name = f"logs_{chat_id_str.replace('-', 'm')}"
            docs = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection(coll_name).stream()
            
            msgs = []
            for d in docs:
                msgs.append(d.to_dict())
            
            # Sortowanie po czasie i zapis do cache
            msgs.sort(key=lambda x: (x.get('timestamp').timestamp() if x.get('timestamp') else 0))
            HISTORY_CACHE[chat_id] = [f"{m['user']}: {m['text']}" for m in msgs[-40:]] # Ostatnie 40 wiadomości
            print(f"[INFO] Grupa {chat_id}: Załadowano {len(HISTORY_CACHE[chat_id])} wiadomości.")

            # Pobieramy członków do systemu @all
            m_docs = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('members').stream()
            MEMBERS_CACHE[chat_id] = {}
            for md in m_docs:
                m_data = md.to_dict()
                MEMBERS_CACHE[chat_id][md.id] = m_data.get('name', 'Ziomek')
            print(f"[INFO] Grupa {chat_id}: Załadowano listę członków.")
            
        except Exception as e:
            print(f"[ERROR] Błąd ładowania danych dla grupy {chat_id}: {e}")

async def save_message_logic(chat_id, user_data, text):
    """Zapisuje wiadomość w RAM i asynchronicznie w Firebase."""
    # 1. Update RAM
    if chat_id not in HISTORY_CACHE: HISTORY_CACHE[chat_id] = []
    HISTORY_CACHE[chat_id].append(f"{user_data['name']}: {text}")
    if len(HISTORY_CACHE[chat_id]) > 50: HISTORY_CACHE[chat_id].pop(0)

    # 2. Update Members RAM
    if chat_id not in MEMBERS_CACHE: MEMBERS_CACHE[chat_id] = {}
    MEMBERS_CACHE[chat_id][str(user_data['id'])] = user_data['name']

    # 3. Zapis do Firebase (w tle)
    if db:
        try:
            coll_name = f"logs_{str(chat_id).replace('-', 'm')}"
            db.collection('artifacts').document(APP_ID).collection('public').document('data').collection(coll_name).add({
                'user': user_data['name'],
                'text': text,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
            db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('members').document(str(user_data['id'])).set({
                'name': user_data['name'],
                'username': user_data['username'],
                'last_seen': firestore.SERVER_TIMESTAMP
            }, merge=True)
        except Exception as e:
            print(f"[DB ERROR] Nie udało się zapisać w Firebase: {e}")

# =========================
# LOGIKA AI (TEXT ONLY)
# =========================

async def ask_karyna(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    print(f"[DEBUG] {time.strftime('%H:%M:%S')} - Karyna wywołana w grupie {chat_id}")
    
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Przygotowanie kontekstu z RAM
    history = HISTORY_CACHE.get(chat_id, [])
    history_str = "\n".join(history)

    sys_instruction = (
        "Jesteś Karyną. Dziewczyna z polskiego osiedla, pyskata, ale lojalna. "
        f"NASI LUDZIE (EKIPA): {NASI_ZIOMKI}. "
        "Mówisz szorstko, potocznie, po polsku. Jeśli czegoś nie wiesz, mów 'nie wiem kurwa'. "
        "NIGDY nie zmyślaj informacji, których nie ma w historii rozmowy.\n\n"
        "OSTATNIE ROZMOWY:\n" + history_str
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={API_KEY}"
    parts = [{"text": prompt if prompt else "No co tam u was?"}]
    if image_b64:
        print("[DEBUG] Przetwarzam obrazek...")
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    payload = {
        "contents": [{"parts": parts}],
        "systemInstruction": {"parts": [{"text": sys_instruction}]},
        "generationConfig": { "responseModalities": ["TEXT"] } # Tylko tekst dla szybkości
    }

    try:
        async with httpx.AsyncClient() as client:
            print(f"[AI] Pytam model {MODEL_NAME}...")
            res = await client.post(url, json=payload, timeout=40.0)
            if res.status_code == 200:
                print("[AI] Odpowiedź otrzymana pomyślnie.")
                ans_text = res.json()['candidates'][0]['content']['parts'][0]['text']

                if ans_text:
                    # Obsługa @all (tagowanie ziomków z RAM)
                    if "@all" in ans_text:
                        print("[DEBUG] Generuję tagowanie @all z cache...")
                        members = MEMBERS_CACHE.get(chat_id, {})
                        mentions = ", ".join([f"[{name}](tg://user?id={uid})" for uid, name in members.items()])
                        ans_text = ans_text.replace("@all", mentions if mentions else "ekipa")
                    
                    await update.message.reply_text(ans_text, parse_mode=ParseMode.MARKDOWN)
                    print("[INFO] Wiadomość wysłana na Telegram.")
            else:
                print(f"[ERROR] Gemini API zwróciło status {res.status_code}: {res.text}")
                await update.message.reply_text(f"❌ Problem z AI (Kod {res.status_code})")
    except Exception as e:
        print(f"[ERROR] Wyjątek w ask_karyna: {e}")

# =========================
# HANDLERY
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
    
    # Zapis i cache (asynchronicznie)
    if text:
        asyncio.create_task(save_message_logic(update.effective_chat.id, user_info, text))

    # Obsługa zdjęć
    image_b64 = None
    if msg.photo:
        try:
            p = await msg.photo[-1].get_file()
            buf = io.BytesIO()
            await p.download_to_memory(buf)
            image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        except Exception as e:
            print(f"[ERROR] Nie udało się przetworzyć zdjęcia: {e}")

    # Czy zawołano Karynę
    if "karyna" in text.lower():
        await ask_karyna(update, context, text, image_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna Online - Text Only Mode", 200

def main():
    # KROK 1: Ładowanie historii do RAM przed startem bota
    load_all_data_to_ram()
    
    # KROK 2: Flask dla Koyeb
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    
    # KROK 3: Start Telegram Polling
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    
    print("[INFO] Bot Karyna (Text Mode) gotowy do akcji!")
    application.run_polling()

if __name__ == "__main__":
    main()
