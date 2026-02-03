import os
import asyncio
import sys
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

# --- POPRAWKA DLA KOYEB ---
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

# Globalna lista linii z pliku wiedzy
KNOWLEDGE_LINES = []

def load_knowledge():
    """Wczytuje wyczyszczony plik tekstowy."""
    if os.path.exists("knowledge.txt"):
        try:
            with open("knowledge.txt", "r", encoding="utf-8") as f:
                lines = f.readlines()
                # Czyścimy puste linie
                return [l.strip() for l in lines if l.strip()]
        except Exception as e:
            print(f"Błąd wczytywania knowledge.txt: {e}")
            return []
    print("BŁĄD: Nie znaleziono pliku knowledge.txt!")
    return []

KNOWLEDGE_LINES = load_knowledge()
print(f"Załadowano {len(KNOWLEDGE_LINES)} linii wiedzy.")

# =========================
# INTELIGENTNE SZUKANIE (RAG)
# =========================
def get_context(query, max_chars=20000):
    """Szuka w pliku tekstowym linii pasujących do pytania."""
    # Słowa kluczowe (min. 4 litery)
    keywords = re.findall(r'\b\w{4,}\b', query.lower())
    if not keywords:
        # Jeśli nie ma słów kluczowych, dajemy ostatnie 100 linii jako ogólny kontekst
        return "\n".join(KNOWLEDGE_LINES[-100:])

    matches = []
    current_len = 0
    # Przeszukujemy od najnowszych wpisów (od dołu pliku)
    for line in reversed(KNOWLEDGE_LINES):
        if any(kw in line.lower() for kw in keywords):
            matches.append(line)
            current_len += len(line)
            if current_len > max_chars:
                break
    
    if not matches:
        return "\n".join(KNOWLEDGE_LINES[-50:])
    
    return "\n".join(reversed(matches))

# =========================
# KONFIGURACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)

def get_model(context_text):
    # Twój spersonalizowany charakter bota
    sys_prompt = (
        "Jesteś wyluzowanym asystentem na grupie Telegram. "
        "Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć (używaj 'kurwa'), "
        "ale nie obrażaj użytkownika i nie nazywaj go debilem. "
        "Odpowiadaj krótko i zwięźle. PISZ ZAWSZE PO POLSKU. "
        "Jeśli w dostarczonych danych nie ma odpowiedzi, powiedz szczerze, że nie wiesz. "
        "Nie wymyślaj niczego, czego nie ma w logach.\n\n"
        f"Wiedza z logów gry:\n{context_text}"
    )
    return genai.GenerativeModel(model_name="gemini-1.5-flash", system_instruction=sys_prompt)

# =========================
# SERWER WWW I LOGIKA TG
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

        # Wyciągamy tylko pasujące fragmenty z 3MB pliku
        context_data = get_context(user_query)
        model = get_model(context_data)

        try:
            # Generowanie odpowiedzi
            response = await asyncio.to_thread(model.generate_content, user_query)
            if response and response.text:
                await msg.reply_text(response.text)
            else:
                await msg.reply_text("AI milczy. Spróbuj zadać pytanie inaczej.")
        except Exception as e:
            err = str(e)
            if "429" in err:
                await msg.reply_text("Kurwa, za dużo pytań. Poczekaj minutę.")
            else:
                await msg.reply_text(f"Błąd: {err[:60]}")

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
