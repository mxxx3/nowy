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

# --- ZARZĄDZANIE BAZĄ DANYCH (DYSK KOYEB) ---
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
        "✅ **Bot Oznaczania Aktywny**\n\n"
        f"👥 Osób w bazie tej grupy: `{len(group_members)}`\n"
        "Napisz `@all`, aby ich oznaczyć."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# --- GŁÓWNA OBSŁUGA WIADOMOŚCI ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS:
        return

    chat_id = str(update.effective_chat.id)
    user_name = msg.from_user.full_name or "Ziomek"
    user_id = str(msg.from_user.id)
    text = (msg.text or msg.caption or "").lower()

    # 1. Zapisz/Aktualizuj osobę w bazie
    db_data = load_db()
    if chat_id not in db_data:
        db_data[chat_id] = {}
    
    # Zapisujemy ID i Nazwę (żeby oznaczanie działało nawet bez username)
    db_data[chat_id][user_id] = user_name
    save_db(db_data)

    # 2. Reakcja na @all
    if "@all" in text:
        log(f"Użyto @all w grupie {chat_id} przez {user_name}")
        
        members = db_data.get(chat_id, {})
        if not members:
            await update.message.reply_text("Baza jest pusta. Niech ziomki coś napiszą!")
            return

        # Budowanie listy oznaczeń
        # Format [Nazwa](tg://user?id=ID) tworzy klikalny link w Markdown
        mention_list = []
        for uid, name in members.items():
            mention_list.append(f"[{name}](tg://user?id={uid})")
        
        # Telegram ma limit wielkości jednej wiadomości, więc łączymy to w czytelny sposób
        mentions_text = "📣 **WEZWANIE EKIPY:**\n\n" + ", ".join(mention_list)
        
        try:
            await update.message.reply_text(mentions_text, parse_mode=ParseMode.MARKDOWN)
            log("Wysłano oznaczenia do wszystkich.")
        except Exception as e:
            log(f"Błąd wysyłania oznaczeń: {e}")
            # Fallback jeśli Markdown zawiedzie przez dziwne znaki w imionach
            await update.message.reply_text("📣 @all - wbijajcie!")

# --- SERWER DO HEALTH CHECK (KOYEB) ---
app = Flask(__name__)
@app.route("/")
def home():
    return "Mention Bot is Running", 200

def main():
    log(">>> START BOTA (MENTION ALL ONLY) <<<")
    
    # Uruchomienie Flask w tle
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    
    # Konfiguracja Telegrama
    application = ApplicationBuilder().token(TG_TOKEN).build()
    
    # Handler komendy /status
    application.add_handler(CommandHandler("status", status_command))
    
    # Handler wszystkich wiadomości (do zbierania ID i reagowania na @all)
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO, handle_message))
    
    log(">>> KONFIGURACJA ZAKOŃCZONA - NASŁUCHUJĘ <<<")
    application.run_polling()

if __name__ == "__main__":
    main()
