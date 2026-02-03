import os
import asyncio
import sys
import json
import time
import re
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
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]

def parse_telegram_json(file_path):
    if not os.path.exists(file_path):
        return ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        messages = data.get("messages", [])
        extracted = []
        for m in messages:
            if m.get("type") == "message" and m.get("text"):
                sender = m.get("from", "Anonim")
                text_field = m.get("text")
                if isinstance(text_field, list):
                    clean_text = "".join([part["text"] if isinstance(part, dict) else str(part) for part in text_field])
                else:
                    clean_text = str(text_field)
                extracted.append(f"{sender}: {clean_text}")
        return "\n".join(extracted)
    except:
        return ""

print("Ładowanie bazy wiedzy...")
# Ograniczamy tekst, żeby nie zapychać limitu TPM (Tokens Per Minute) zbyt szybko
KNOWLEDGE_1 = parse_telegram_json("result.json")
KNOWLEDGE_2 = parse_telegram_json("result1.json")
FULL_KNOWLEDGE = (KNOWLEDGE_1 + "\n" + KNOWLEDGE_2)[:400000]

SYSTEM_PROMPT = f"""
Jesteś wyluzowanym asystentem na grupie Telegram. 
Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć, 
ale nie obrażaj użytkownika i nie nazywaj go debilem.
Odpowiadaj krótko i zwięźle po polsku.

TWOJA WIEDZA O GRZE (Z logów):
{FULL_KNOWLEDGE} 

ZASADY:
1. PISZ PO POLSKU.
2. Odpowiadaj krótko.
3. Jeśli nie znasz odpowiedzi, powiedz "nie wiem". Nie zmyślaj.
"""

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_best_model():
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        # Flash jest najlepszy pod kątem limitów (429 zdarza się rzadziej)
        for m in available_models:
            if "gemini-1.5-flash" in m:
                return genai.GenerativeModel(model_name=m, system_instruction=SYSTEM_PROMPT)
        return genai.GenerativeModel(model_name=available_models[0], system_instruction=SYSTEM_PROMPT)
    except:
        return genai.GenerativeModel(model_name="gemini-1.5-flash", system_instruction=SYSTEM_PROMPT)

model = get_best_model()

async def safe_generate(query):
    """Generowanie z obsługą błędu 429 (Resource Exhausted)."""
    max_retries = 3
    for i in range(max_retries):
        try:
            response = model.generate_content(query)
            return response.text if response and response.text else "AI nie wypluło tekstu."
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg:
                # Wyciągamy czas czekania z błędu lub czekamy domyślnie
                wait_time = 10 * (i + 1)
                print(f"Limit przekroczony (429). Czekam {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue
            return f"Błąd AI: {err_msg}"
    return "Kurwa, za dużo zapytań naraz. Spróbuj za minutę."

# =========================
# SERWER WWW I LOGIKA
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is running!", 200

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    if update.effective_chat.id not in ALLOWED_GROUPS: return

    if msg.text.lower().startswith('/gpt'):
        prompt = msg.text.replace('/gpt', '', 1).strip()
        query = prompt if prompt else "Co ciekawego wiesz o grze?"
        answer = await safe_generate(query)
        await msg.reply_text(answer)

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
