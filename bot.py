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

# --- POPRAWKA DLA KOYEB/WINDOWS ---
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

print("Wczytywanie bazy wiedzy...")
load_and_parse_json("result.json")
load_and_parse_json("result1.json")
print(f"Załadowano {len(ALL_MESSAGES)} wiadomości.")

# =========================
# INTELIGENTNE WYSZUKIWANIE (RAG)
# =========================
def get_relevant_context(query, max_chars=18000):
    # Szukamy słów kluczowych (min. 4 litery)
    keywords = re.findall(r'\b\w{4,}\b', query.lower())
    if not keywords:
        return "\n".join(ALL_MESSAGES[-100:])
    
    found = []
    length = 0
    # Przeszukujemy od najświeższych wiadomości
    for msg in reversed(ALL_MESSAGES):
        if any(kw in msg.lower() for kw in keywords):
            found.append(msg)
            length += len(msg)
            if length > max_chars:
                break
    
    # Jeśli nic nie znaleziono, dajemy ostatnie 50 wiadomości jako ogólny kontekst
    return "\n".join(reversed(found)) if found else "\n".join(ALL_MESSAGES[-50:])

# =========================
# KONFIGURACJA MODELU FLASH 1.5
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_flash_model(context_text):
    # Twoja instrukcja systemowa - krótko, szorstko, bez obrażania
    sys_instruction = (
        "Jesteś wyluzowanym asystentem na grupie Telegram. "
        "Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć (np. kurwa), "
        "ale nie obrażaj użytkownika i nie nazywaj go debilem. "
        "Piszesz ZAWSZE PO POLSKU. Odpowiadaj krótko i zwięźle. "
        "Jeśli nie znasz odpowiedzi na podstawie logów, powiedz szczerze: 'nie wiem'. "
        "Nie wymyślaj informacji bez dowodów w historii.\n\n"
        f"OTO KONTEKST Z TWOICH PLIKÓW JSON:\n{context_text}"
    )

    # Używamy tylko i wyłącznie Gemini 1.5 Flash
    return genai.GenerativeModel(
        model_name="gemini-1.5-flash", 
        system_instruction=sys_instruction
    )

# =========================
# LOGIKA I SERWER
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot Flash is running!", 200

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    if update.effective_chat.id not in ALLOWED_GROUPS: return

    if msg.text.lower().startswith('/gpt'):
        user_query = msg.text.replace('/gpt', '', 1).strip()
        if not user_query:
            await msg.reply_text("O co kurwa pytasz? Napisz coś po /gpt.")
            return

        # Pobierz tylko to co ważne, żeby nie palić kredytów
        context_data = get_relevant_context(user_query)
        model = get_flash_model(context_data)

        try:
            # Generowanie odpowiedzi przez Flash 1.5
            response = model.generate_content(user_query)
            if response and response.text:
                await msg.reply_text(response.text)
            else:
                await msg.reply_text("AI milczy. Spróbuj zadać pytanie inaczej.")
        except Exception as e:
            err = str(e)
            if "429" in err:
                await msg.reply_text("Limit zapytań przekroczony. Poczekaj z minutę.")
            elif "404" in err:
                await msg.reply_text("Błąd 404: Google nie widzi modelu Flash 1.5. Sprawdź klucz API.")
            else:
                await msg.reply_text(f"Błąd techniczny: {err[:80]}")

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
