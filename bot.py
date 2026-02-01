import os
import asyncio
import sys
from threading import Thread
from flask import Flask
import google.generativeai as genai
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- POPRAWKA DLA WINDOWS ---
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
GEMINI_KEY = "AIzaSyDiiKs5Y-6CBuTLpSwhc_pQJP5rWb_S4F8"
TG_TOKEN = "8254563937:AAF4C2z0npXhN1mIp4E0xBi8Ug9n4pdZz-0"

# IDENTYFIKATOR TWOJEJ GRUPY
ALLOWED_GROUP_ID = -1003676480681

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_best_model():
    """Automatyczny wybór najlepszego dostępnego modelu."""
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        priority_list = ["2.5-flash", "2.0-flash", "1.5-flash", "flash-latest", "gemini-pro"]
        for priority in priority_list:
            for model_name in available_models:
                if priority in model_name.lower():
                    return genai.GenerativeModel(model_name)
        return genai.GenerativeModel(available_models[0])
    except Exception:
        return genai.GenerativeModel("models/gemini-1.5-flash")

model = get_best_model()

# =========================
# SERWER WWW (Koyeb)
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is running!", 200

def run_flask():
    try: app.run(host="0.0.0.0", port=8080)
    except: pass

# =========================
# OBSŁUGA WIADOMOŚCI
# =========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. Sprawdzamy czy to wiadomość tekstowa
    if not update.message or not update.message.text:
        return

    # 2. Blokada na konkretną grupę
    # Sprawdzamy, czy ID czatu zgadza się z Twoją grupą
    if update.effective_chat.id != ALLOWED_GROUP_ID:
        # Opcjonalnie: można tu wysłać info, że bot tu nie działa, 
        # ale lepiej milczeć, żeby nie spamować innych grup.
        return

    user_text = update.message.text

    # 3. Sprawdzamy czy wiadomość zaczyna się od /gpt
    if not user_text.lower().startswith('/gpt'):
        return

    # Usuwamy "/gpt" z początku zapytania
    # Usuwamy też ewentualne "/gpt@nazwa_bota"
    prompt = user_text.replace('/gpt', '', 1).strip()
    if prompt.startswith(f"@{context.bot.username}"):
        prompt = prompt.replace(f"@{context.bot.username}", "", 1).strip()

    # Jeśli ktoś wpisał samo /gpt bez pytania
    if not prompt:
        await update.message.reply_text("Wpisz pytanie po komendzie /gpt, np.: /gpt Jak działa AI?")
        return

    try:
        # Generowanie odpowiedzi
        response = model.generate_content(prompt)
        if response and response.text:
            await update.message.reply_text(response.text)
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            await update.message.reply_text("Zbyt wiele zapytań! Poczekaj chwilę.")
        else:
            print(f"Błąd: {e}")
            await update.message.reply_text("Wystąpił błąd podczas generowania odpowiedzi.")

# =========================
# START BOTA
# =========================
async def start_bot():
    Thread(target=run_flask, daemon=True).start()
    print(f"Uruchamiam bota (Tylko dla grupy: {ALLOWED_GROUP_ID})...")
    
    application = ApplicationBuilder().token(TG_TOKEN).build()
    
    # Obsługujemy wszystkie wiadomości, ale filtracja dzieje się w handle_message
    application.add_handler(MessageHandler(filters.TEXT, handle_message))

    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        while True: await asyncio.sleep(3600)

if __name__ == "__main__":
    if os.name == 'nt': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try: asyncio.run(start_bot())
    except KeyboardInterrupt: pass
