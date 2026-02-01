import os
import asyncio
from flask import Flask
from threading import Thread
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# --- KONFIGURACJA ---
# Wkleiłem Twoje klucze bezpośrednio, aby uniknąć błędów ze zmiennymi środowiskowymi
GEMINI_KEY = 'AIzaSyAznQEeX_BmFEHAmFAwvOtw50tqhIcFb8I'
TG_TOKEN = '8254563937:AAF4C2z0npXhN1mIp4E0xBi8Ug9n4pdZz-0'

# Inicjalizacja Gemini
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# Serwer Flask dla Koyeb (musi słuchać na porcie 8080)
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    # Koyeb domyślnie oczekuje ruchu na porcie 8080
    app.run(host='0.0.0.0', port=8080)

# Obsługa wiadomości z Telegrama
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Sprawdzamy czy to wiadomość tekstowa
    if not update.message or not update.message.text:
        return

    text = update.message.text
    
    try:
        # Generowanie odpowiedzi przez AI
        response = model.generate_content(text)
        await update.message.reply_text(response.text)
    except Exception as e:
        print(f"Błąd Gemini: {e}")
        await update.message.reply_text("Wystąpił błąd podczas generowania odpowiedzi.")

async def main():
    # Uruchomienie serwera WWW w osobnym wątku
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Budowanie aplikacji Telegram
    print("Uruchamiam bota...")
    application = ApplicationBuilder().token(TG_TOKEN).build()

    # Handler dla wiadomości tekstowych
    text_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    application.add_handler(text_handler)

    # Start bota
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        # Utrzymanie bota przy życiu
        while True:
            await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
