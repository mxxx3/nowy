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

# --- POPRAWKA DLA WINDOWS (Błąd strefy czasowej) ---
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

# LISTA DOZWOLONYCH GRUP
ALLOWED_GROUPS = [-1003676480681, -1002159478145]

# --- CHARAKTER BOTA (SYSTEM PROMPT) ---
# Tutaj wpisz instrukcje dotyczące osobowości bota
SYSTEM_PROMPT = """
Głupi, Bardzo dużo mocno przeklina, zamiast kurła mówi kurwa, mówi na mnie debil, i robi długie odpowiedzi jak paragon z biedronki
"""

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_best_model():
    """Wybiera najlepszy dostępny model i ustawia mu charakter (System Instruction)."""
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        priority_list = ["2.5-flash", "2.0-flash", "1.5-flash", "flash-latest", "gemini-pro"]
        
        selected_model_name = "models/gemini-1.5-flash"
        for priority in priority_list:
            for model_name in available_models:
                if priority in model_name.lower():
                    selected_model_name = model_name
                    break
            else: continue
            break
        
        print(f"Inicjalizacja modelu: {selected_model_name} z charakterem.")
        # Przekazujemy charakter (system_instruction) podczas tworzenia modelu
        return genai.GenerativeModel(
            model_name=selected_model_name,
            system_instruction=SYSTEM_PROMPT
        )
    except Exception as e:
        print(f"Błąd inicjalizacji modelu: {e}")
        return genai.GenerativeModel("models/gemini-1.5-flash", system_instruction=SYSTEM_PROMPT)

model = get_best_model()

# =========================
# SERWER WWW (Dla Koyeb)
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!", 200

def run_flask():
    try:
        app.run(host="0.0.0.0", port=8080)
    except Exception:
        pass

# =========================
# OBSŁUGA WIADOMOŚCI
# =========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    # 1. SPRAWDZENIE GRUPY
    if update.effective_chat.id not in ALLOWED_GROUPS:
        return

    user_text = update.message.text

    # 2. SPRAWDZENIE KOMENDY /gpt
    if not user_text.lower().startswith('/gpt'):
        return

    # Wyciąganie treści zapytania (usuwamy /gpt)
    prompt = user_text.replace('/gpt', '', 1).strip()
    
    # Obsługa /gpt@nazwa_bota
    if prompt.startswith(f"@{context.bot.username}"):
        prompt = prompt.replace(f"@{context.bot.username}", "", 1).strip()

    if not prompt:
        await update.message.reply_text("Wpisz pytanie po /gpt, np: /gpt Jak działa słońce?")
        return

    try:
        # Generowanie odpowiedzi przez Gemini
        response = model.generate_content(prompt)
        if response and response.text:
            await update.message.reply_text(response.text)
        else:
            await update.message.reply_text("AI nie zwróciło odpowiedzi (możliwa blokada treści).")

    except Exception as e:
        print(f"Błąd Gemini: {e}")
        if "429" in str(e):
            await update.message.reply_text("Zbyt wiele pytań naraz! Poczekaj chwilę.")
        else:
            await update.message.reply_text("Wystąpił błąd podczas generowania odpowiedzi.")

# =========================
# START BOTA
# =========================
async def start_bot():
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    print(f"Uruchamiam bota dla grup: {ALLOWED_GROUPS}...")
    application = ApplicationBuilder().token(TG_TOKEN).build()
    
    application.add_handler(MessageHandler(filters.TEXT, handle_message))

    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        pass
