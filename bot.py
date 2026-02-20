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

# Stała lista ziomków (tylko jako kontekst dla AI, bot ma tego nie wypisywać jako tagi)
NASI_ZIOMKI = "Gal, Karol, Nassar, Łukasz, DonMacias, Polski Ninja, Oliv, One Way Ticket, Bajtkojn, Tomek, Mando, mateusz, Pdablju, XDemon, Michal K, SHARK, KrisFX, Halison, Wariat95, Shadows, andzia, Marzena, Kornello, Tomasz, DonMakveli, Lucifer, Stara Janina, Matis64, Kama, Kicia, Kociamber Auuu, KERTH, Ulalala, Dorcia, Kuba, Damian, Marshmallow, KarolCarlos, PIRATEPpkas Pkas, Maniek, HuntFiWariat9501, Krystiano1993, Jazda jazda, Dottie, Khent"

# --- SYSTEM LOGOWANIA ---
def log(msg):
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

# --- ZARZĄDZANIE HISTORIĄ I EKIPĄ NA DYSKU ---
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
    if update.effective_chat.id not in ALLOWED_GROUPS:
        return
    
    db_data = load_db()
    chat_id = str(update.effective_chat.id)
    group_data = db_data.get(chat_id, {"msgs": [], "members": {}})
    num_members = len(group_data.get("members", {}))
    
    status_msg = (
        "📊 **Status Ekipy (Radar)**\n\n"
        f"👥 Ziomków w bazie do oznaczania: `{num_members}`\n"
        "Tylko te osoby zostaną oznaczone przez `@all`.\n\n"
        "_Osoby spoza bazy nie będą wypisywane tekstowo._"
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

    # 1. Zapisz wiadomość i ziomka na dysku
    db_data = load_db()
    if chat_id not in db_data:
        db_data[chat_id] = {"msgs": [], "members": {}}
    
    # Dodaj ziomka do bazy (prawdziwe dane do oznaczania)
    db_data[chat_id]["members"][user_id] = user_name
    
    # Historia
    db_data[chat_id]["msgs"].append({"u": user_name, "t": text, "ts": time.time()})
    if len(db_data[chat_id]["msgs"]) > 80:
        db_data[chat_id]["msgs"].pop(0)
    
    save_db(db_data)

    # 2. Sprawdź czy zawołano Karynę
    if "karyna" in text.lower():
        log(f"INFO: Karyna wywołana w {chat_id}. Pytam AI...")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        history_msgs = db_data[chat_id]["msgs"]
        history_str = "\n".join([f"{m['u']}: {m['t']}" for m in history_msgs[-25:]])

        sys_prompt = (
            "Jesteś Karyną. Pyskata dziewczyna z polskiego osiedla. Nie jesteś AI. "
            f"TWOJA EKIPA: {NASI_ZIOMKI}.\n"
            "ZASADA 1: Jeśli chcesz zawołać ziomków, użyj TYLKO słowa '@all'.\n"
            "ZASADA 2: NIGDY nie wypisuj ręcznie listy imion ziomków z listy powyżej. System sam podmieni @all na prawdziwe tagi.\n"
            "ZASADA 3: Mów szorstko, potocznie. Jeśli czegoś nie wiesz, mów 'nie wiem kurwa'.\n\n"
            "HISTORIA ROZMÓW:\n" + history_str
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
                    
                    # Logika podmiany @all na prawdziwe mentions z bazy
                    if "@all" in ans.lower():
                        members = db_data[chat_id].get("members", {})
                        if members:
                            mention_list = [f"[{name}](tg://user?id={uid})" for uid, name in members.items()]
                            mentions_str = ", ".join(mention_list)
                            # Podmieniamy @all (i różne wersje wielkości liter) na listę linków
                            import re
                            ans = re.sub(r'@all', mentions_str, ans, flags=re.IGNORECASE)
                        else:
                            ans = ans.replace("@all", "ekipa")

                    await update.message.reply_text(ans, parse_mode=ParseMode.MARKDOWN)
                    log("SUCCESS: Odpowiedź wysłana.")
                else:
                    log(f"BŁĄD AI {res.status_code}")
                    await update.message.reply_text(f"❌ Coś mnie przycięło (Kod {res.status_code})")
            except Exception as e:
                log(f"WYJĄTEK AI: {e}")

# --- SERWER ---
app = Flask(__name__)
@app.route("/")
def home(): return "Karyna Tagging Fix Active", 200

def main():
    log(">>> START BOTA KARYNA (TAGGING FIX) <<<")
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    application.run_polling()

if __name__ == "__main__":
    main()
