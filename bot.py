import os
import asyncio
from flask import Flask
from threading import Thread
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# --- KONFIGURACJA KLUCZY ---
# Możesz wpisać je tutaj bezpośrednio w cudzysłowie:
GEMINI_KEY = 'AIzaSyAznQEeX_BmFEHAmFAwvOtw50tqhIcFb8I'
TG_TOKEN = '8254563937:AAF4C2z0npXhN1mIp4E0xBi8Ug9n4pdZz-0'

# Konfiguracja modelu Gemini
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# Prosty serwer WWW (wymagany przez Koyeb, aby nie wyłączył bota)
app = Flask('')
@app.route('/')
def home():
    return "Bot działa poprawnie!"

def run_web():
    # Koyeb wymaga nasłuchiwania na porcie 8080
    app.run(host='0.0.0.0', port=8080)

# Funkcja obsługująca wiadomości na Telegramie
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Sprawdzamy, czy wiadomość zawiera tekst
    if update.message and update.message.text:
        try:
            # Wysyłamy tekst do Gemini
            chat_response = model.generate_content(update.message.text)
            # Odpowiadamy użytkownikowi na Telegramie
            await update.message.reply_text(chat_response.text)
        except Exception as e:
            print(f"Błąd Gemini: {e}")
            await update.message.reply_text("Przepraszam, mam mały problem z myśleniem. Spróbuj za chwilę!")

if __name__ == '__main__':
    # 1. Uruchom serwer WWW w osobnym wątku
    Thread(target=run_web).start()
    
    # 2. Skonfiguruj bota Telegram
    print("Uruchamiam bota...")
    application = ApplicationBuilder().token(TG_TOKEN).build()
    
    # Obsługuj wszystkie wiadomości tekstowe (z wyjątkiem komend)
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    # 3. Zacznij nasłuchiwanie wiadomości

    application.run_polling()

