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

# --- LOGI ---
def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- BAZA DANYCH NA DYSKU ---
def load_db():
    if not os.path.exists(DB_PATH):
        return {}
    try:
        with open(DB_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_db(data):
    try:
        with open(DB_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"Błąd zapisu bazy: {e}")

# --- KOMENDA /STATUS ---
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ALLOWED_GROUPS:
        return
    db_data = load_db()
    chat_id = str(update.effective_chat.id)
    group_members = db_data.get(chat_id, {})
    
    msg = (
        "📢 **Bot Oznaczania Ekipy**\n"
        f"Liczba osób na radarze: `{len(group_members)}`"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# --- OBSŁUGA WIADOMOŚCI ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS:
        return

    chat_id = str(update.effective_chat.id)
    user_id = str(msg.from_user.id)
    user_name = msg.from_user.full_name or "Ziomek"
    username = msg.from_user.username
    
    text = (msg.text or msg.caption or "").lower()

    # 1. Rejestracja/Aktualizacja ziomka w bazie
    db_data = load_db()
    if chat_id not in db_data:
        db_data[chat_id] = {}
    
    db_data[chat_id][user_id] = {
        "n": user_name,
        "u": username
    }
    save_db(db_data)

    # 2. Reakcja na @all
    if "@all" in text:
        log(f"Wywołanie @all przez {user_name}")
        members = db_data.get(chat_id, {})
        
        if not members:
            return

        mention_list = []
        for uid, info in members.items():
            u_name = info.get("n", "Ziomek")
            u_nick = info.get("u")
            
            if u_nick:
                # Oznaczanie przez @username
                mention_list.append(f"@{u_nick}")
            else:
                # Oznaczanie przez link (dla osób bez nicku)
                mention_list.append(f"[{u_name}](tg://user?id={uid})")
        
        final_text = "📣 **EKIPA WBIJAĆ!**\n\n" + ", ".join(mention_list)
        
        try:
            await update.message.reply_text(final_text, parse_mode=ParseMode.MARKDOWN)
            log("Oznaczenia wysłane.")
        except Exception as e:
            log(f"Błąd wysyłki: {e}")
            await update.message.reply_text("📣 @all - wbijajcie natychmiast!")

# --- SERWER ---
app = Flask(__name__)
@app.route("/")
def home(): return "Mention Bot Active", 200

def main():
    log(">>> START BOTA (TYLKO OZNACZANIE) <<<")
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.ALL, handle_message))
    
    log(">>> BOT NASŁUCHUJE <<<")
    application.run_polling()

if __name__ == "__main__":
    main()
