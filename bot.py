import os
import asyncio
import json
import base64
import httpx
import time
import sys
import re
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

# --- SYSTEM LOGOWANIA ---
def log(msg):
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

# --- ZARZĄDZANIE DYSKIEM ---
def load_db():
    if not os.path.exists(DB_PATH):
        return {}
    try:
        with open(DB_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log(f"BŁĄD odczytu: {e}")
        return {}

def save_db(data):
    try:
        with open(DB_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"BŁĄD zapisu: {e}")

# --- HANDLER STATUSU ---
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ALLOWED_GROUPS:
        return
    
    db_data = load_db()
    chat_id = str(update.effective_chat.id)
    group_data = db_data.get(chat_id, {"msgs": [], "members": {}})
    members = group_data.get("members", {})
    
    status_msg = (
        "📊 **Radar Karyny**\n\n"
        f"👥 Ziomków w bazie: `{len(members)}`\n"
        "Tylko te osoby zostaną oznaczone przez `@all`.\n\n"
        "Osoby na radarze:\n" + (", ".join(members.values()) if members else "_Brak_")
    )
    await update.message.reply_text(status_msg, parse_mode=ParseMode.MARKDOWN)

# --- GŁÓWNA LOGIKA ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS:
        return

    chat_id = str(update.effective_chat.id)
    user_name = msg.from_user.full_name or "Ziomek"
    user_id = str(msg.from_user.id)
    text = msg.text or msg.caption or ""

    if not text:
        return

    # 1. Zapisz na dysku (Rejestracja ziomka i logowanie)
    db_data = load_db()
    if chat_id not in db_data:
        db_data[chat_id] = {"msgs": [], "members": {}}
    
    # Rejestracja/Aktualizacja danych ziomka
    db_data[chat_id]["members"][user_id] = user_name
    
    # Historia wiadomości
    db_data[chat_id]["msgs"].append({"u": user_name, "t": text, "ts": time.time()})
    if len(db_data[chat_id]["msgs"]) > 60:
        db_data[chat_id]["msgs"].pop(0)
    
    save_db(db_data)

    # 2. Sprawdzenie triggerów (Karyna lub @all)
    is_karyna_called = "karyna" in text.lower()
    is_all_called = "@all" in text.lower()

    if is_karyna_called or is_all_called:
        log(f"Wywołanie: {user_name} (Karyna: {is_karyna_called}, @all: {is_all_called})")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # Przygotuj historię z dysku
        history_msgs = db_data[chat_id]["msgs"]
        history_str = "\n".join([f"{m['u']}: {m['t']}" for m in history_msgs[-25:]])

        # Instrukcja systemowa - bardzo rygorystyczna
        sys_prompt = (
            "Jesteś Karyną, pyskatą dziewczyną z polskiego osiedla. Nie jesteś sztuczną inteligencją.\n"
            "ZASADA 1: Jeśli chcesz zawołać ziomków, napisz w treści '@all'. NIGDY nie wypisuj listy imion ręcznie.\n"
            "ZASADA 2: Nie znasz listy imion ziomków na pamięć. Operuj tylko na tym, co widzisz w historii rozmowy.\n"
            "ZASADA 3: Mów szorstko, potocznie, po polsku. Jeśli czegoś nie wiesz, mów 'nie wiem kurwa'.\n"
            f"ZASADA 4: Jeśli użytkownik w swojej wiadomości użył '@all', Ty OBOWIĄZKOWO również musisz użyć '@all' w swojej odpowiedzi, aby system mógł oznaczyć ekipę.\n\n"
            "HISTORIA ROZMOWY:\n" + history_str
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
                    
                    # Wymuszenie @all, jeśli użytkownik o to prosił, a Karyna zapomniała
                    if is_all_called and "@all" not in ans.lower():
                        ans += "\n\n@all wbijajcie!"

                    # Podmiana @all na prawdziwe tagi z bazy lokalnej
                    if "@all" in ans.lower():
                        members = db_data[chat_id].get("members", {})
                        if members:
                            mention_list = [f"[{name}](tg://user?id={uid})" for uid, name in members.items()]
                            mentions_str = ", ".join(mention_list)
                            # Zastępujemy wszystkie wystąpienia @all listą tagów
                            ans = re.sub(r'@all', mentions_str, ans, flags=re.IGNORECASE)
                        else:
                            ans = re.sub(r'@all', "ekipa", ans, flags=re.IGNORECASE)

                    await update.message.reply_text(ans, parse_mode=ParseMode.MARKDOWN)
                    log("Wysłano odpowiedź z tagami.")
                    
                    # Zapisz odpowiedź do historii na dysku
                    db_data = load_db()
                    if chat_id not in db_data: db_data[chat_id] = {"msgs": [], "members": {}}
                    db_data[chat_id]["msgs"].append({"u": "Karyna", "t": ans, "ts": time.time()})
                    save_db(db_data)
                else:
                    log(f"Błąd AI: {res.status_code}")
            except Exception as e:
                log(f"Wyjątek: {e}")

# --- SERWER ---
app = Flask(__name__)
@app.route("/")
def home(): return "Karyna Tagging Fix 2.0 Active", 200

def main():
    log(">>> START BOTA (NAPRAWA @ALL) <<<")
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    application.run_polling()

if __name__ == "__main__":
    main()
