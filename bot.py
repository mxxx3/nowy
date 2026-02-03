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

# --- POPRAWKA DLA KOYEB (Błąd strefy czasowej) ---
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

# Globalna lista wiadomości
ALL_MESSAGES = []

def load_knowledge():
    """Wczytuje dane z plików JSON do pamięci RAM."""
    for file_name in ["result.json", "result1.json"]:
        if os.path.exists(file_name):
            try:
                with open(file_name, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    messages = data.get("messages", [])
                    for m in messages:
                        if m.get("type") == "message" and m.get("text"):
                            sender = m.get("from", "Anonim")
                            raw_text = m.get("text")
                            # Obsługa tekstu, który może być listą w JSON
                            if isinstance(raw_text, list):
                                clean_text = "".join([p["text"] if isinstance(p, dict) else str(p) for p in raw_text])
                            else:
                                clean_text = str(raw_text)
                            ALL_MESSAGES.append(f"{sender}: {clean_text}")
                print(f"Załadowano: {file_name}")
            except Exception as e:
                print(f"Błąd pliku {file_name}: {e}")

load_knowledge()

# =========================
# WYSZUKIWANIE KONTEKSTU (RAG)
# =========================
def find_relevant_info(query, max_chars=12000):
    """Szuka w historii tylko tych fragmentów, które pasują do pytania."""
    # Słowa kluczowe min. 4 litery
    words = re.findall(r'\b\w{4,}\b', query.lower())
    if not words:
        return "\n".join(ALL_MESSAGES[-60:]) # Ostatnie 60 jeśli brak słów kluczowych

    matched = []
    current_len = 0
    # Przeszukujemy od końca (najnowsze najważniejsze)
    for msg in reversed(ALL_MESSAGES):
        if any(w in msg.lower() for w in words):
            matched.append(msg)
            current_len += len(msg)
            if current_len > max_chars:
                break
    
    return "\n".join(reversed(matched)) if matched else "\n".join(ALL_MESSAGES[-30:])

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

# Charakter bota - zgodnie z Twoją prośbą
BASE_SYSTEM_PROMPT = (
    "Jesteś wyluzowanym asystentem na grupie Telegram. "
    "Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć (np. 'kurwa'), "
    "ale nie obrażaj użytkownika i nie nazywaj go debilem. "
    "Odpowiadaj krótko i zwięźle, bez lania wody. "
    "PISZ ZAWSZE PO POLSKU. "
    "Jeśli w dostarczonych logach nie ma odpowiedzi, powiedz szczerze: 'Nie wiem kurwa, nikt o tym nie pisał'. "
    "Nigdy nie wymyślaj informacji o grze, jeśli nie masz na to dowodów."
)

# Inicjalizujemy model RAZ przy starcie, aby uniknąć błędów 404 przy każdym zapytaniu
# Używamy bezpiecznej nazwy modelu
try:
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash", 
        system_instruction=BASE_SYSTEM_PROMPT
    )
except:
    # Fallback na wypadek specyficznych ustawień środowiska
    model = genai.GenerativeModel(model_name="models/gemini-1.5-flash", system_instruction=BASE_SYSTEM_PROMPT)

# =========================
# LOGIKA I SERWER
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is running!", 200

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    if update.effective_chat.id not in ALLOWED_GROUPS: return

    if msg.text.lower().startswith('/gpt'):
        user_query = msg.text.replace('/gpt', '', 1).strip()
        if not user_query:
            await msg.reply_text("O co kurwa pytasz? Napisz coś po /gpt.")
            return

        # Pobieramy fragmenty JSON pasujące do pytania
        context_data = find_relevant_info(user_query)
        
        # Budujemy prompt łączący wiedzę z pytaniem
        full_prompt = (
            f"Oto fragmenty rozmów z grupy, które mogą Ci pomóc:\n{context_data}\n\n"
            f"Pytanie użytkownika: {user_query}"
        )

        try:
            # Generowanie odpowiedzi
            response = await asyncio.to_thread(model.generate_content, full_prompt)
            
            if response and response.text:
                await msg.reply_text(response.text)
            else:
                await msg.reply_text("AI milczy. Może spytaj o coś innego.")
                
        except Exception as e:
            error_str = str(e)
            if "429" in error_str:
                await msg.reply_text("Kurwa, za dużo pytań naraz. Poczekaj z minutę.")
            elif "404" in error_str:
                await msg.reply_text("Błąd modelu (404). Coś nie tak z nazwą modelu Gemini.")
            else:
                await msg.reply_text(f"Błąd techniczny: {error_str[:60]}")

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), lambda u, c: None)) # Ignoruj zwykłe teksty
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    
    print("Bot z bazą JSON gotowy do akcji!")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
