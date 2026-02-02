import os
import asyncio
import sys
import io
import base64
import requests
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

# --- OPCJA NUKLEARNA DLA WINDOWS ---
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

ALLOWED_GROUPS = [-1003676480681, -1002159478145]
MAX_HISTORY = 200
chat_histories = {}

# --- CHARAKTER BOTA ---
SYSTEM_PROMPT = """
Jesteś wyluzowanym asystentem na grupie Telegram. 
Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć, 
ale nie obrażaj użytkownika i nie nazywaj go debilem
"""

if not GEMINI_KEY or not TG_TOKEN:
    print("BŁĄD: Brak zmiennych środowiskowych w Koyeb!")
    sys.exit(1)

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_best_model():
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        priority_list = ["2.5-flash", "2.0-flash", "1.5-flash", "flash-latest"]
        selected = next((m for p in priority_list for m in available_models if p in m.lower()), "models/gemini-1.5-flash")
        return genai.GenerativeModel(model_name=selected, system_instruction=SYSTEM_PROMPT)
    except:
        return genai.GenerativeModel("models/gemini-1.5-flash", system_instruction=SYSTEM_PROMPT)

model = get_best_model()

# =========================
# GENEROWANIE OBRAZU Z KONTEKSTEM
# =========================
def generate_image_with_context(user_request, history_list):
    """Analizuje historię i tworzy obraz na jej podstawie."""
    history_text = "\n".join(history_list)
    
    # Prosimy Gemini o stworzenie promptu dla Imagen na podstawie historii
    analysis_prompt = (
        f"Na podstawie poniższej historii rozmów z grupy:\n{history_text}\n\n"
        f"Oraz prośby użytkownika: '{user_request if user_request else 'Stwórz obraz podsumowujący tę rozmowę'}'\n\n"
        "Stwórz bardzo szczegółowy, artystyczny opis obrazu po angielsku (Image Prompt). "
        "Zwróć TYLKO opis po angielsku, bez żadnych dodatkowych komentarzy."
    )
    
    try:
        analysis_model = genai.GenerativeModel("gemini-1.5-flash")
        eng_prompt_resp = analysis_model.generate_content(analysis_prompt)
        english_prompt = eng_prompt_resp.text.strip()
        print(f"Wygenerowany prompt dla Imagen: {english_prompt}")
    except Exception as e:
        print(f"Błąd analizy historii: {e}")
        english_prompt = user_request if user_request else "Abstract representation of a digital conversation"

    # Wywołanie Imagen 4.0
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict?key={GEMINI_KEY}"
    payload = {
        "instances": [{"prompt": english_prompt}],
        "parameters": {"sampleCount": 1}
    }
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            if "predictions" in data and len(data["predictions"]) > 0:
                img_b64 = data["predictions"][0]["bytesBase64Encoded"]
                return io.BytesIO(base64.b64decode(img_b64)), english_prompt
    except Exception as e:
        print(f"Błąd API Imagen: {e}")
    return None, None

# =========================
# SERWER FLASK
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is running!", 200
def run_flask():
    try: app.run(host="0.0.0.0", port=8080)
    except: pass

# =========================
# HANDLER WIADOMOŚCI
# =========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_GROUPS: return

    incoming_text = msg.text or msg.caption or ""
    user_name = msg.from_user.first_name or "Użytkownik"

    if chat_id not in chat_histories:
        chat_histories[chat_id] = deque(maxlen=MAX_HISTORY)

    # 1. GENEROWANIE OBRAZU (/img)
    if incoming_text.lower().startswith('/img'):
        user_req = incoming_text.replace('/img', '', 1).strip()
        status_msg = await msg.reply_text("Dobra, kurwa, czytam o czym pisaliście i zaraz to narysuję...")
        
        # Pobieramy historię
        history = list(chat_histories[chat_id])
        
        # Generujemy obraz w tle
        loop = asyncio.get_event_loop()
        img_data, used_prompt = await loop.run_in_executor(None, generate_image_with_context, user_req, history)
        
        await status_msg.delete()
        
        if img_data:
            caption = f"Namalowałem to, co wywnioskowałem z waszego bełkotu.\n\nPrompt AI: {used_prompt[:200]}..."
            await msg.reply_photo(photo=img_data, caption=caption)
        else:
            await msg.reply_text("Coś się zjebało i pędzel mi pękł. Spróbuj później.")

    # 2. CZAT AI (/gpt)
    elif incoming_text.lower().startswith('/gpt'):
        prompt = incoming_text.replace('/gpt', '', 1).strip()
        history_context = "\n".join(list(chat_histories[chat_id]))
        final_prompt = prompt if prompt else "Streść mi o czym tu pisali."
        
        try:
            content = [f"HISTORIA GRUPY:\n{history_context}\n\nPYTANIE: {final_prompt}"]
            if msg.photo:
                file = await msg.photo[-1].get_file()
                content.append({"mime_type": "image/jpeg", "data": bytes(await file.download_as_bytearray())})
            
            response = model.generate_content(content)
            if response and response.text:
                await msg.reply_text(response.text)
        except:
            await msg.reply_text("Błąd przy gadaniu z AI.")

    # 3. ZAPIS DO HISTORII
    else:
        if incoming_text:
            chat_histories[chat_id].append(f"{user_name}: {incoming_text}")

# =========================
# START
# =========================
def main():
    Thread(target=run_flask, daemon=True).start()
    print("Uruchamiam bota z WIZJĄ i INTELIGENTNYM IMAGENEM...")
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
