import os
import asyncio
import sys
import json
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

# --- POPRAWKA DLA WINDOWS/KOYEB ---
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

# Globalna baza wiadomości
ALL_MESSAGES = []

def load_and_parse_json(file_path):
    if not os.path.exists(file_path):
        return
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        messages = data.get("messages", [])
        for m in messages:
            if m.get("type") == "message" and m.get("text"):
                sender = m.get("from", "Anonim")
                text_field = m.get("text")
                if isinstance(text_field, list):
                    clean_text = "".join([part["text"] if isinstance(part, dict) else str(part) for part in text_field])
                else:
                    clean_text = str(text_field)
                ALL_MESSAGES.append(f"{sender}: {clean_text}")
    except Exception as e:
        print(f"Błąd ładowania {file_path}: {e}")

print("Ładowanie bazy wiedzy...")
load_and_parse_json("result.json")
load_and_parse_json("result1.json")
print(f"Załadowano {len(ALL_MESSAGES)} wiadomości.")

# =========================
# WYSZUKIWANIE KONTEKSTU (RAG)
# =========================
def get_relevant_context(query, max_chars=12000):
    keywords = re.findall(r'\b\w{4,}\b', query.lower())
    if not keywords:
        return "\n".join(ALL_MESSAGES[-80:])
    
    found = []
    length = 0
    # Przeszukujemy od najnowszych
    for msg in reversed(ALL_MESSAGES):
        if any(kw in msg.lower() for kw in keywords):
            found.append(msg)
            length += len(msg)
            if length > max_chars:
                break
    
    return "\n".join(reversed(found)) if found else "\n".join(ALL_MESSAGES[-40:])

# =========================
# INTELIGENTNY WYBÓR MODELU
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_chat_model(context_text):
    # Twój spersonalizowany prompt
    sys_instruction = (
        "Jesteś wyluzowanym asystentem na grupie Telegram. "
        "Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć (używaj 'kurwa'), "
        "ale nie obrażaj użytkownika i nie nazywaj go debilem. Odpowiadaj krótko i zwięźle. "
        "Piszesz po polsku za każdym razem. Jeśli nie znasz odpowiedzi, po prostu powiedz, że nie wiesz. "
        "Nie wymyślaj informacji bez wiarygodnych dowodów w logach.\n\n"
        f"OTO KONTEKST Z GRUPY:\n{context_text}"
    )

    try:
        # Próbujemy znaleźć najlepszą dostępną nazwę modelu
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # Lista preferencji (Flash jest najlepszy pod koszty i limity)
        target_models = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-pro"]
        
        selected_name = "gemini-1.5-flash" # Domyślny fallback
        for target in target_models:
            for m_name in models:
                if target in m_name:
                    selected_name = m_name
                    break
            else: continue
            break
            
        return genai.GenerativeModel(model_name=selected_name, system_instruction=sys_instruction)
    except Exception as e:
        print(f"Błąd przy wyborze modelu: {e}")
        return genai.GenerativeModel(model_name="gemini-1.5-flash", system_instruction=sys_instruction)

# =========================
# SERWER I OBSŁUGA TG
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is live!", 200

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    if update.effective_chat.id not in ALLOWED_GROUPS: return

    if msg.text.lower().startswith('/gpt'):
        user_query = msg.text.replace('/gpt', '', 1).strip()
        if not user_query:
            await msg.reply_text("Kurwa, napisz o co Ci chodzi po tym /gpt.")
            return

        context_data = get_relevant_context(user_query)
        model = get_chat_model(context_data)

        try:
            response = model.generate_content(user_query)
            if response and response.text:
                await msg.reply_text(response.text)
            else:
                await msg.reply_text("AI milczy. Może spytaj o coś innego.")
        except Exception as e:
            err = str(e)
            if "429" in err:
                await msg.reply_text("Limit zapytań przekroczony. Daj mi odpocząć minutę.")
            elif "404" in err:
                await msg.reply_text("Błąd modelu (404). Google coś kombinuje z nazwami.")
            else:
                await msg.reply_text(f"Coś się zjebało: {err[:50]}")

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
