import os
import asyncio
import sys
import io
from threading import Thread
from flask import Flask
from collections import deque
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

# LISTA TWOICH GRUP
ALLOWED_GROUPS = [-1003676480681, -1002159478145]

# Limit 100 wiadomości w pamięci RAM
MAX_HISTORY = 100
chat_histories = {}

# --- CHARAKTER BOTA (SYSTEM PROMPT) ---
SYSTEM_PROMPT = """
Jesteś wyluzowanym asystentem na grupie Telegram. 
Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć (używaj 'kurwa' zamiast 'kurła'), 
ale nie obrażaj użytkownika i nie nazywaj go debilem
"""

# Sprawdzenie kluczy w Koyeb
if not GEMINI_KEY or not TG_TOKEN:
    print("BŁĄD: Ustaw GEMINI_API_KEY i TELEGRAM_TOKEN w panelu Koyeb!")
    sys.exit(1)

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_best_model():
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        priority_list = ["2.5-flash", "2.0-flash", "1.5-flash", "flash-latest"]
        
        selected_model_name = "models/gemini-1.5-flash"
        for priority in priority_list:
            for model_name in available_models:
                if priority in model_name.lower():
                    selected_model_name = model_name
                    break
            else: continue
            break
        
        return genai.GenerativeModel(
            model_name=selected_model_name,
            system_instruction=SYSTEM_PROMPT
        )
    except Exception:
        return genai.GenerativeModel("models/gemini-1.5-flash", system_instruction=SYSTEM_PROMPT)

model = get_best_model()

# =========================
# SERWER WWW (Dla Koyeb Health Check)
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
    msg = update.message
    if not msg: return

    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_GROUPS:
        return

    incoming_text = msg.text or msg.caption or ""
    user_name = msg.from_user.first_name or "Użytkownik"

    if chat_id not in chat_histories:
        chat_histories[chat_id] = deque(maxlen=MAX_HISTORY)

    # REAKCJA NA KOMENDĘ /GPT
    if incoming_text.lower().startswith('/gpt'):
        prompt = incoming_text.replace('/gpt', '', 1).strip()
        
        # Przygotowanie historii
        history_list = list(chat_histories[chat_id])
        history_context = "\n".join(history_list)
        
        # Budujemy zapytanie. Jeśli prompt jest pusty, prosimy o streszczenie.
        final_prompt = prompt if prompt else "Streść mi o czym tu kurwa pisali jak mnie nie było."
        
        full_query = (
            f"KONTEKST OSTATNICH ROZMÓW:\n{history_context}\n\n"
            f"ZAPYTANIE OD {user_name}: {final_prompt}"
        )

        try:
            content_to_send = [full_query]
            
            # Obsługa zdjęcia ze streszczeniem
            if msg.photo:
                photo_file = await msg.photo[-1].get_file()
                img_bytes = await photo_file.download_as_bytearray()
                content_to_send.append({"mime_type": "image/jpeg", "data": bytes(img_bytes)})

            response = model.generate_content(content_to_send)
            
            if response and response.text:
                await msg.reply_text(response.text)
            else:
                await msg.reply_text("AI milczy, pewnie znowu jakieś blokady treści.")
        except Exception as e:
            print(f"Błąd Gemini: {e}")
            await msg.reply_text("Coś się kurwa wywaliło przy generowaniu odpowiedzi.")
    
    # ZAPISYWANIE DO PAMIĘCI (Wiadomości bez /gpt)
    else:
        if incoming_text:
            # Zapisujemy format "Imię: Treść"
            formatted = f"{user_name}: {incoming_text}"
            chat_histories[chat_id].append(formatted)

# =========================
# START
# =========================
def main():
    Thread(target=run_flask, daemon=True).start()
    print(f"Uruchamiam bota z pamięcią 100 wiadomości...")
    
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
