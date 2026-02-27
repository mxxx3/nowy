import os
import json
import time
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# --- KONFIGURACJA ---
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
DB_PATH = "ekipa.json"

# --- SYSTEM LOGOWANIA ---
def log(msg):
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

# --- ZARZĄDZANIE BAZĄ DANYCH ---
def load_db():
    if not os.path.exists(DB_PATH):
        return {}
    try:
        with open(DB_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log(f"BŁĄD odczytu bazy: {e}")
        return {}

def save_db(data):
    try:
        with open(DB_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"BŁĄD zapisu bazy: {e}")

# --- KOMENDA STATUSU ---
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ALLOWED_GROUPS:
        return
    
    db_data = load_db()
    chat_id = str(update.effective_chat.id)
    group_members = db_data.get(chat_id, {})
    
    msg = (
        "✅ **Bot Oznaczania Ekipy**\n\n"
        f"👥 Ziomków w bazie: `{len(group_members)}`\n"
        "Napisz `@all`, aby ich zawołać."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# --- GŁÓWNA OBSŁUGA WIADOMOŚCI ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS:
        return

    chat_id = str(update.effective_chat.id)
    user_id = str(msg.from_user.id)
    user_name = msg.from_user.full_name or "Ziomek"
    user_username = msg.from_user.username  # Pobieramy @nick
    
    text = (msg.text or msg.caption or "").lower()

    # 1. Zapisujemy ziomka (z nickiem lub bez)
    db_data = load_db()
    if chat_id not in db_data:
        db_data[chat_id] = {}
    
    db_data[chat_id][user_id] = {
        "name": user_name,
        "username": user_username
    }
    save_db(db_data)

    # 2. Reakcja na @all
    if "@all" in text:
        log(f"Oznaczanie @all w grupie {chat_id}")
        
        members = db_data.get(chat_id, {})
        if not members:
            await update.message.reply_text("Baza jest pusta, nikt jeszcze nic nie napisał.")
            return

        # Budowanie listy oznaczania
        mentions = []
        for uid, info in members.items():
            username = info.get("username")
            name = info.get("name", "Ziomek")
            
            if username:
                # Jeśli ma nick, używamy @nick
                mentions.append(f"@{username}")
            else:
                # Jeśli nie ma nicku, musimy użyć linku po ID żeby go zawołać
                mentions.append(f"[{name}](tg://user?id={uid})")
        
        header = "📣 **WBIJAĆ NA REJON!**\n\n"
        final_text = header + ", ".join(mentions)
        
        try:
            # Używamy Markdown, żeby oznaczanie osób bez nicków działało
            await update.message.reply_text(final_text, parse_mode=ParseMode.MARKDOWN)
            log("Oznaczenia wysłane pomyślnie.")
        except Exception as e:
            log(f"Błąd wysyłania: {e}")
            await update.message.reply_text("📣 @all - wbijajcie!")

# --- SERWER HEALTH CHECK ---
app = Flask(__name__)
@app.route("/")
def home():
    return "Mention Bot @Username Ready", 200

def main():
    log(">>> START BOTA (WERSJA @USERNAME) <<<")
    
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO, handle_message))
    
    log(">>> NASŁUCHIWANIE AKTYWNE <<<")
    application.run_polling()

if __name__ == "__main__":
    main()
