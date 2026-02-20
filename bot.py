import os
import asyncio
import json
import base64
import httpx
import time
import sys
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- KONFIGURACJA ---
API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
MODEL_NAME = "gemini-3-flash-preview"
DB_PATH = "karyna_history.json" # Plik na dysku Koyeb

# Ziomki (Wiedza stała)
NASI_ZIOMKI = "Gal, Karol, Nassar, Łukasz, DonMacias, Polski Ninja, Oliv, One Way Ticket, Bajtkojn, Tomek, Mando, mateusz, Pdablju, XDemon, Michal K, SHARK, KrisFX, Halison, Wariat95, Shadows, andzia, Marzena, Kornello, Tomasz, DonMakveli, Lucifer, Stara Janina, Matis64, Kama, Kicia, Kociamber Auuu, KERTH, Ulalala, Dorcia, Kuba, Damian, Marshmallow, KarolCarlos, PIRATEPpkas Pkas, Maniek, HuntFiWariat9501, Krystiano1993, Jazda jazda, Dottie, Khent"

# --- SYSTEM LOGOWANIA ---
def log(msg):
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

# --- ZARZĄDZANIE HISTORIĄ NA DYSKU ---
def load_db():
    if not os.path.exists(DB_PATH):
        log("Plik historii nie istnieje. Tworzę nowy.")
        return {}
    try:
        with open(DB_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log(f"BŁĄD odczytu pliku: {e}")
        return {}

def save_db(data):
    try:
        with open(DB_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"BŁĄD zapisu na dysk: {e}")

# --- LOGIKA BOTA ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS:
        return

    chat_id = str(update.effective_chat.id)
    user = msg.from_user.full_name or "Ziomek"
    text = msg.text or msg.caption or ""

    if not text:
        return

    # 1. Zapisz wiadomość na dysku
    db_data = load_db()
    if chat_id not in db_data:
        db_data[chat_id] = []
    
    db_data[chat_id].append({"u": user, "t": text, "ts": time.time()})
    
    # Trzymamy ostatnie 100 wiadomości na grupę, żeby nie zapchać RAM-u przy odczycie
    if len(db_data[chat_id]) > 100:
        db_data[chat_id].pop(0)
    
    save_db(db_data)
    log(f"Zapisano wiadomość od {user} w grupie {chat_id}")

    # 2. Sprawdź czy zawołano Karynę
    if "karyna" in text.lower():
        log(f"INFO: Karyna wywołana przez {user}. Start AI...")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # Przygotuj historię dla AI
        history_msgs = db_data.get(chat_id, [])
        history_str = "\n".join([f"{m['u']}: {m['t']}" for m in history_msgs[-30:]])

        sys_prompt = (
            "Jesteś Karyną. Dziewczyna z polskiego osiedla, pyskata, lojalna ziomalka. "
            f"TWOI LUDZIE: {NASI_ZIOMKI}. Mówisz szorstko, potocznie, po polsku. "
            "Jeśli nie znasz odpowiedzi, po prostu powiedz 'nie wiem kurwa'. "
            "NIGDY nie zmyślaj informacji, których nie ma w historii.\n\n"
            "OSTATNIE ROZMOWY:\n" + history_str
        )

        image_b64 = None
        if msg.photo:
            try:
                p = await msg.photo[-1].get_file()
                image_b64 = base64.b64encode(await p.download_as_bytearray()).decode('utf-8')
                log("DEBUG: Przetworzono zdjęcie.")
            except Exception as e:
                log(f"Błąd zdjęcia: {e}")

        # Zapytanie do Gemini
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={API_KEY}"
        contents = [{"parts": [{"text": text}]}]
        if image_b64:
            contents[0]["parts"].append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": sys_prompt}]},
            "generationConfig": { "responseModalities": ["TEXT"] }
        }

        async with httpx.AsyncClient() as client:
            try:
                log(f"AI: Wysyłam zapytanie do {MODEL_NAME}...")
                res = await client.post(url, json=payload, timeout=40.0)
                
                if res.status_code == 200:
                    ans = res.json()['candidates'][0]['content']['parts'][0]['text']
                    await update.message.reply_text(ans, parse_mode=ParseMode.MARKDOWN)
                    log("SUCCESS: Odpowiedź wysłana na Telegram.")
                    
                    # Zapisz też odpowiedź Karyny do historii
                    db_data[chat_id].append({"u": "Karyna", "t": ans, "ts": time.time()})
                    save_db(db_data)
                else:
                    log(f"BŁĄD AI {res.status_code}: {res.text}")
                    await update.message.reply_text(f"❌ Coś mnie przycięło (Błąd {res.status_code})")
            except Exception as e:
                log(f"WYJĄTEK AI: {e}")
                await update.message.reply_text("❌ Wywaliło mnie na zakręcie. Spróbuj potem.")

# --- SERWER WWW ---
app = Flask(__name__)
@app.route("/")
def home(): 
    return "Karyna Disk Mode Active - Firebase Disabled", 200

def main():
    log(">>> START BOTA KARYNA (DISK MODE) <<<")
    
    # Start Flask w tle
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    
    # Start Telegram
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    log(">>> KONIEC KONFIGURACJI - NASŁUCHUJĘ <<<")
    application.run_polling()

if __name__ == "__main__":
    main()
