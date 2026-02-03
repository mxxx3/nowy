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

# Globalna lista wszystkich wiadomości z plików
ALL_MESSAGES = []

def load_and_parse_json(file_path):
    """Wczytuje wiadomości do listy, aby łatwo je przeszukiwać."""
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

print("Ładowanie bazy wiedzy do pamięci...")
load_and_parse_json("result.json")
load_and_parse_json("result1.json")
print(f"Załadowano łącznie {len(ALL_MESSAGES)} wiadomości.")

# =========================
# LOGIKA WYSZUKIWANIA (Mini-RAG)
# =========================
def get_relevant_context(query, max_chars=15000):
    """Szuka fragmentów rozmów pasujących do pytania."""
    # Wyciągamy słowa kluczowe (min. 3 litery)
    keywords = re.findall(r'\b\w{3,}\b', query.lower())
    if not keywords:
        # Jeśli brak słów kluczowych, bierzemy ostatnie 100 wiadomości
        return "\n".join(ALL_MESSAGES[-100:])

    relevant_chunks = []
    current_length = 0
    
    # Przeszukujemy bazę (od najnowszych, żeby mieć świeże info)
    for msg in reversed(ALL_MESSAGES):
        if any(kw in msg.lower() for kw in keywords):
            relevant_chunks.append(msg)
            current_length += len(msg)
            if current_length > max_chars:
                break
    
    if not relevant_chunks:
        return "\n".join(ALL_MESSAGES[-50:]) # Nic nie znaleziono, daj trochę historii
        
    return "\n".join(reversed(relevant_chunks))

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_model(context_text):
    """Tworzy model z dynamicznym kontekstem."""
    system_instruction = f"""
    Jesteś wyluzowanym asystentem na grupie Telegram. 
    Masz szorstki styl, możesz czasem przekląć, ale nie obrażaj użytkownika i nie nazywaj go debilem.
    Odpowiadaj krótko i wyłącznie po polsku.

    OTRZYMUJESZ FRAGMENTY LOGÓW PASUJĄCE DO PYTANIA:
    {context_text}

    ZASADY:
    1. Odpowiadaj na podstawie logów.
    2. Jeśli w tych logach nie ma odpowiedzi, powiedz szczerze, że nie wiesz. Nie zmyślaj.
    3. Bądź zwięzły.
    """
    return genai.GenerativeModel(model_name="gemini-1.5-flash", system_instruction=system_instruction)

# =========================
# SERWER WWW I OBSŁUGA TG
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is healthy!", 200

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    if update.effective_chat.id not in ALLOWED_GROUPS: return

    if msg.text.lower().startswith('/gpt'):
        user_query = msg.text.replace('/gpt', '', 1).strip()
        if not user_query:
            await msg.reply_text("O co konkretnie pytasz? Napisz coś po /gpt.")
            return

        # 1. Znajdź tylko to, co ważne
        relevant_context = get_relevant_context(user_query)
        
        # 2. Skonfiguruj model z tym konkretnym kawałkiem wiedzy
        dynamic_model = get_model(relevant_context)
        
        try:
            # 3. Zapytaj AI
            response = dynamic_model.generate_content(user_query)
            if response and response.text:
                await msg.reply_text(response.text)
            else:
                await msg.reply_text("AI milczy, spróbuj inaczej sformułować pytanie.")
        except Exception as e:
            if "429" in str(e):
                await msg.reply_text("Kurwa, za dużo pytań naraz. Poczekaj z pół minuty.")
            else:
                await msg.reply_text(f"Błąd AI: {str(e)[:100]}")

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
