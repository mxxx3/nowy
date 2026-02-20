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
    CommandHandler,
    ContextTypes,
    filters,
)

# --- KONFIGURACJA ---
API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
MODEL_NAME = "gemini-3-flash-preview"
DB_PATH = "karyna_history.json"

# Ziomki (Wiedza stała)
NASI_ZIOMKI = "Gal, Karol, Nassar, Łukasz, DonMacias, Polski Ninja, Oliv, One Way Ticket, Bajtkojn, Tomek, Mando, mateusz, Pdablju, XDemon, Michal K, SHARK, KrisFX, Halison, Wariat95, Shadows, andzia, Marzena, Kornello, Tomasz, DonMakveli, Lucifer, Stara Janina, Matis64, Kama, Kicia, Kociamber Auuu, KERTH, Ulalala, Dorcia, Kuba, Damian, Marshmallow, KarolCarlos, PIRATEPpkas Pkas, Maniek, HuntFiWariat9501, Krystiano1993, Jazda jazda, Dottie, Khent"

# --- SYSTEM LOGOWANIA ---
def log(msg):
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

# --- ZARZĄDZANIE HISTORIĄ NA DYSKU ---
def load_db():
    if not os.path.exists(DB_PATH):
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

# --- HANDLERY KOMEND ---
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sprawdza stan pliku historii na dysku Koyeb."""
    if update.effective_chat.id not in ALLOWED_GROUPS:
        return

    log(f"Sprawdzanie statusu przez {update.effective_user.full_name}")
    
    db_data = load_db()
    file_exists = os.path.exists(DB_PATH)
    file_size = os.path.getsize(DB_PATH) if file_exists else 0
    num_groups = len(db_data)
    
    status_msg = (
        "📊 **Status Karyny (Disk Mode)**\n\n"
        f"📂 Plik bazy: `{'✅ Istnieje' if file_exists else '❌ Brak'}`\n"
        f"💾 Rozmiar: `{file_size / 1024:.2f} KB`\n"
        f"👥 Grupy w pamięci: `{num_groups}`\n"
        f"🕒 Czas bota: `{time.strftime('%H:%M:%S')}`\n\n"
        "Wszystko leci na dysk Koyeb, mordo!"
    )
    await update.message.reply_text(status_msg, parse_mode=ParseMode.MARKDOWN)

# --- GŁÓWNA LOGIKA ---
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
    
    # Limit historii na grupę
    if len(db_data[chat_id]) > 100:
        db_data[chat_id].pop(0)
    
    save_db(db_data)

    # 2. Sprawdź czy zawołano Karynę
    if "karyna" in text.lower():
        log(f"INFO: Karyna wywołana w {chat_id}. Pytam AI...")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # Przygotuj historię
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
            except: pass

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
                res = await client.post(url, json=payload, timeout=40.0)
                if res.status_code == 200:
                    ans = res.json()['candidates'][0]['content']['parts'][0]['text']
                    await update.message.reply_text(ans, parse_mode=ParseMode.MARKDOWN)
                    log("SUCCESS: Odpowiedź wysłana.")
                    
                    # Zapisz odpowiedź do historii
                    db_data = load_db()
                    if chat_id not in db_data: db_data[chat_id] = []
                    db_data[chat_id].append({"u": "Karyna", "t": ans, "ts": time.time()})
                    save_db(db_data)
                else:
                    log(f"BŁĄD AI {res.status_code}")
                    await update.message.reply_text(f"❌ Coś mnie przycięło (Kod {res.status_code})")
            except Exception as e:
                log(f"WYJĄTEK AI: {e}")

# --- SERWER WWW ---
app = Flask(__name__)
@app.route("/")
def home(): 
    return "Karyna Disk Mode Active", 200

def main():
    log(">>> START BOTA KARYNA <<<")
    
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    
    application = ApplicationBuilder().token(TG_TOKEN).build()
    
    # Komenda statusu
    application.add_handler(CommandHandler("status", status_command))
    # Reszta wiadomości
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    log(">>> KONFIGURACJA GOTOWA <<<")
    application.run_polling()

if __name__ == "__main__":
    main()
