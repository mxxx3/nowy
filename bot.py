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
# Rozwiązuje błąd "Only timezones from the pytz library are supported" na Windowsie
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
# Tokeny i klucze (Zawsze trzymaj je w bezpiecznym miejscu)
GEMINI_KEY = "AIzaSyDiiKs5Y-6CBuTLpSwhc_pQJP5rWb_S4F8"
TG_TOKEN = "8254563937:AAF4C2z0npXhN1mIp4E0xBi8Ug9n4pdZz-0"

# LISTA DOZWOLONYCH GRUP
ALLOWED_GROUPS = [-1003676480681, -1002159478145]

# --- CHARAKTER BOTA (SYSTEM PROMPT) ---
# Definiuje osobowość i sposób odpowiadania bota
SYSTEM_PROMPT = """
Głupi, Bardzo dużo mocno przeklina, zamiast kurła mówi kurwa, mówi na mnie debil, i robi długie odpowiedzi jak paragon z biedronki
"""

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_best_model():
    """Automatycznie wybiera najlepszy dostępny model dla Twojego klucza API."""
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
        
        print(f"Inicjalizacja modelu: {selected_model_name} z Twoim charakterem.")
        return genai.GenerativeModel(
            model_name=selected_model_name,
            system_instruction=SYSTEM_PROMPT
        )
    except Exception as e:
        print(f"Błąd podczas wyboru modelu: {e}")
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
        # Koyeb wymaga nasłuchiwania na porcie 8080
        app.run(host="0.0.0.0", port=8080)
    except Exception:
        pass

# =========================
# OBSŁUGA WIADOMOŚCI
# =========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ignoruj wiadomości bez tekstu lub od samego siebie
    if not update.message or not update.message.text:
        return

    # 1. SPRAWDZENIE CZY GRUPA JEST DOZWOLONA
    if update.effective_chat.id not in ALLOWED_GROUPS:
        return

    user_text = update.message.text

    # 2. SPRAWDZENIE CZY WIADOMOŚĆ ZACZYNA SIĘ OD KOMENDY /gpt
    if not user_text.lower().startswith('/gpt'):
        return

    # Wyciąganie treści zapytania (usuwamy "/gpt" z początku)
    prompt = user_text.replace('/gpt', '', 1).strip()
    
    # Obsługa przypadku /gpt@nazwa_bota
    if prompt.startswith(f"@{context.bot.username}"):
        prompt = prompt.replace(f"@{context.bot.username}", "", 1).strip()

    if not prompt:
        await update.message.reply_text("Wpisz pytanie po /gpt, debilu!")
        return

    try:
        # Przesyłamy zapytanie do Gemini
        response = model.generate_content(prompt)
        if response and response.text:
            # Wysyłamy wygenerowaną odpowiedź
            await update.message.reply_text(response.text)
        else:
            await update.message.reply_text("AI nic nie wypluło, pewnie coś znowu zepsułeś.")

    except Exception as e:
        print(f"Błąd podczas generowania: {e}")
        if "429" in str(e):
            await update.message.reply_text("Czekaj kurwa, za dużo pytań naraz!")
        else:
            await update.message.reply_text("Coś się zjebało w Gemini, spróbuj później.")

# =========================
# URUCHOMIENIE BOTA
# =========================
async def start_bot():
    # Start serwera Flask w tle (dla Koyeb)
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    print(f"Uruchamiam bota dla grup: {ALLOWED_GROUPS}")
    
    # Inicjalizacja aplikacji Telegram
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    
    # Dodanie handlera dla wszystkich wiadomości tekstowych
    application.add_handler(MessageHandler(filters.TEXT, handle_message))

    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        # Nieskończona pętla utrzymująca działanie skryptu
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    # Konfiguracja pętli zdarzeń dla systemów Windows
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        print("\nZamykanie bota...")
