import os
import asyncio
import sys
import io
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
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# LISTA DOZWOLONYCH GRUP
ALLOWED_GROUPS = [-1003676480681, -1002159478145]

# --- CHARAKTER BOTA (SYSTEM PROMPT) ---
SYSTEM_PROMPT = """
Jesteś wyluzowanym asystentem na grupie Telegram. 
Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć (używaj 'kurwa' zamiast 'kurła'), 
ale nie obrażaj użytkownika i nie nazywaj go debilem
"""

# Sprawdzenie kluczy
if not GEMINI_KEY or not TG_TOKEN:
    print("BŁĄD: Brakuje zmiennych środowiskowych w Koyeb!")
    sys.exit(1)

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_best_model():
    """Wybiera najlepszy dostępny model z obsługą wizji."""
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        # Gemini 1.5 i 2.x Flash obsługują multimodalność (obraz + tekst)
        priority_list = ["2.5-flash", "2.0-flash", "1.5-flash", "flash-latest"]
        
        selected_model_name = "models/gemini-1.5-flash"
        for priority in priority_list:
            for model_name in available_models:
                if priority in model_name.lower():
                    selected_model_name = model_name
                    break
            else: continue
            break
        
        print(f"Inicjalizacja modelu wizyjnego: {selected_model_name}")
        return genai.GenerativeModel(
            model_name=selected_model_name,
            system_instruction=SYSTEM_PROMPT
        )
    except Exception:
        return genai.GenerativeModel("models/gemini-1.5-flash", system_instruction=SYSTEM_PROMPT)

model = get_best_model()

# =========================
# SERWER WWW (Dla Koyeb)
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is running!", 200

def run_flask():
    try: app.run(host="0.0.0.0", port=8080)
    except: pass

# =========================
# OBSŁUGA WIADOMOŚCI (Tekst i Foto)
# =========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return

    # 1. SPRAWDZENIE GRUPY
    if update.effective_chat.id not in ALLOWED_GROUPS:
        return

    # Pobieramy tekst z wiadomości lub podpisu zdjęcia
    text = msg.text or msg.caption or ""

    # 2. SPRAWDZENIE KOMENDY /gpt
    if not text.lower().startswith('/gpt'):
        return

    # Przygotowanie zapytania (usuwamy /gpt)
    prompt = text.replace('/gpt', '', 1).strip()
    if prompt.startswith(f"@{context.bot.username}"):
        prompt = prompt.replace(f"@{context.bot.username}", "", 1).strip()

    try:
        content_to_send = [prompt if prompt else "Opisz co widzisz na tym obrazku."]
        
        # 3. OBSŁUGA ZDJĘCIA
        if msg.photo:
            # Informacja o przetwarzaniu (opcjonalnie)
            status_msg = await msg.reply_text("Czekaj kurwa, patrzę na to zdjęcie...")
            
            # Pobieramy największy rozmiar zdjęcia
            photo_file = await msg.photo[-1].get_file()
            img_bytearray = await photo_file.download_as_bytearray()
            
            # Dodajemy obraz do zapytania Gemini
            image_part = {
                "mime_type": "image/jpeg",
                "data": bytes(img_bytearray)
            }
            content_to_send.append(image_part)
            
            # Usuwamy status "Czekaj"
            await status_msg.delete()

        # Generowanie odpowiedzi
        response = model.generate_content(content_to_send)
        
        if response and response.text:
            await msg.reply_text(response.text)
        else:
            await msg.reply_text("Dostałem obrazek, ale AI nic nie wypluło. Może zbyt ostry kontent?")

    except Exception as e:
        print(f"Błąd Gemini: {e}")
        if "429" in str(e):
            await msg.reply_text("Zwolnij kurwa! Za dużo obrazków/pytań.")
        else:
            await msg.reply_text("Coś się zjebało przy analizie. Spróbuj później.")

# =========================
# START BOTA
# =========================
def main():
    Thread(target=run_flask, daemon=True).start()
    print(f"Uruchamiam bota z WIZJĄ dla grup: {ALLOWED_GROUPS}")
    
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    
    # MessageHandler teraz reaguje na tekst ORAZ zdjęcia
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
