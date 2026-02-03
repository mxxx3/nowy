import os
import asyncio
import sys
import json
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
    """Wyciąga tekst z pliku JSON eksportu Telegrama."""
    if not os.path.exists(file_path):
        print(f"INFO: Plik {file_path} nie istnieje.")
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
    except Exception as e:
        print(f"BŁĄD przy {file_path}: {e}")
        return ""

# Ładowanie danych z JSONów
print("Ładowanie bazy wiedzy...")
KNOWLEDGE_1 = parse_telegram_json("result.json")
KNOWLEDGE_2 = parse_telegram_json("result1.json")
FULL_KNOWLEDGE = KNOWLEDGE_1 + "\n\n--- DANE Z DRUGIEJ GRUPY ---\n\n" + KNOWLEDGE_2

# --- AKTUALNY SYSTEM PROMPT ---
SYSTEM_PROMPT = f"""
Jesteś wyluzowanym asystentem na grupie Telegram. 
Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć, 
ale nie obrażaj użytkownika i nie nazywaj go debilem.
Odpowiadaj krótko i zwięźle.

TWOJA WIEDZA O GRZE (Dane z logów):
{FULL_KNOWLEDGE[:900000]} 

ZASADY:
1. PISZ PO POLSKU ZA KAŻDYM RAZEM.
2. Odpowiedzi mają być krótkie, bez zbędnego lania wody.
3. Jeśli nie znasz odpowiedzi, po prostu powiedz, że nie wiesz. Nie wymyślaj informacji, jeśli nie masz na nie wiarygodnych dowodów w danych powyżej.
"""

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_best_model():
    """Dobiera dostępny model Gemini dla Twojego klucza."""
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        priority_list = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]
        
        for priority in priority_list:
            for model_name in available_models:
                if priority in model_name:
                    print(f"Używam modelu: {model_name}")
                    return genai.GenerativeModel(model_name=model_name, system_instruction=SYSTEM_PROMPT)
        
        return genai.GenerativeModel(model_name=available_models[0], system_instruction=SYSTEM_PROMPT)
    except Exception as e:
        print(f"Błąd modeli: {e}")
        return genai.GenerativeModel(model_name="gemini-1.5-flash", system_instruction=SYSTEM_PROMPT)

model = get_best_model()

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
        query = prompt if prompt else "Co ciekawego wiesz o tej grze?"
        
        try:
            response = model.generate_content(query)
            if response and response.text:
                await msg.reply_text(response.text)
            else:
                await msg.reply_text("Coś mnie przyblokowało, nie mam odpowiedzi.")
        except Exception as e:
            print(f"Błąd AI: {e}")
            await msg.reply_text("Wystąpił błąd przy łączeniu z AI.")

def main():
    # Uruchomienie serwera flask dla Koyeb w tle
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    
    # Start bota telegramowego
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
