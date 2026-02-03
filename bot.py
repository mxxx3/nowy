import os
import asyncio
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

# --- POPRAWKA DLA KOYEB (Obsługa pętli zdarzeń) ---
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

# Wczytywanie bazy wiedzy z pliku knowledge.txt (3MB)
KNOWLEDGE_LINES = []
if os.path.exists("knowledge.txt"):
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            KNOWLEDGE_LINES = [l.strip() for l in f.readlines() if l.strip()]
        print(f"Załadowano {len(KNOWLEDGE_LINES)} linii z bazy wiedzy.")
    except Exception as e:
        print(f"Błąd wczytywania knowledge.txt: {e}")
else:
    print("BŁĄD: Nie znaleziono pliku knowledge.txt na serwerze!")

# =========================
# INTELIGENTNE WYSZUKIWANIE (RAG)
# =========================
def get_context(query, max_chars=15000):
    """Przeszukuje plik knowledge.txt w poszukiwaniu fragmentów pasujących do pytania."""
    # Słowa kluczowe (min. 4 litery)
    keywords = re.findall(r'\b\w{4,}\b', query.lower())
    
    if not keywords:
        # Jeśli brak słów kluczowych, bierzemy ostatnie 80 linii jako ogólny kontekst
        return "\n".join(KNOWLEDGE_LINES[-80:])

    matches = []
    current_len = 0
    # Przeszukujemy bazę od najświeższych wpisów (od dołu pliku)
    for line in reversed(KNOWLEDGE_LINES):
        if any(kw in line.lower() for kw in keywords):
            matches.append(line)
            current_len += len(line)
            if current_len > max_chars:
                break
    
    return "\n".join(reversed(matches)) if matches else "\n".join(KNOWLEDGE_LINES[-40:])

# =========================
# KONFIGURACJA GEMINI 2.0 FLASH
# =========================
genai.configure(api_key=GEMINI_KEY)

async def ask_gemini_2_0(user_query, context_text):
    # Twój specyficzny charakter bota
    sys_prompt = (
        "Jesteś wyluzowanym asystentem na grupie Telegram. "
        "Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć, "
        "ale NIE obrażaj użytkownika i NIE nazywaj go debilem. "
        "Odpowiadaj krótko, zwięźle i konkretnie. PISZ ZAWSZE PO POLSKU. "
        "Jeśli w dostarczonych logach nie ma odpowiedzi, powiedz szczerze: 'Nie wiem, nikt o tym nie pisał'. "
        "Nie wymyślaj informacji, których nie ma w historii gry.\n\n"
        f"OTO DANE Z TWOJEJ BAZY WIEDZY:\n{context_text}"
    )

    try:
        # Ustawienie modelu Gemini 2.0 Flash
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash", 
            system_instruction=sys_prompt
        )
        
        # Generowanie odpowiedzi (uruchomione w wątku, aby nie blokować bota)
        response = await asyncio.to_thread(model.generate_content, user_query)
        
        if response and response.text:
            return response.text
        return "Coś mnie przyblokowało, nie mam odpowiedzi."
        
    except Exception as e:
        err_str = str(e)
        if "429" in err_str:
            return "Kurwa, za dużo pytań naraz. Dajcie mi minutę oddechu."
        if "404" in err_str:
            return "Błąd 404: Model Gemini 2.0 Flash nie został znaleziony. Sprawdź ustawienia API."
        return f"Wystąpił błąd techniczny: {err_str[:60]}"

# =========================
# SERWER WWW I OBSŁUGA TELEGRAMA
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot Gemini 2.0 Flash is live!", 200

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    
    # Sprawdzanie czy bot ma odpowiedzieć (tylko na dozwolonych grupach)
    if update.effective_chat.id not in ALLOWED_GROUPS: return

    # Bot reaguje tylko na komendę /gpt
    if msg.text.lower().startswith('/gpt'):
        user_query = msg.text.replace('/gpt', '', 1).strip()
        
        if not user_query:
            await msg.reply_text("O co kurwa pytasz? Napisz coś sensownego po /gpt.")
            return

        # 1. Pobierz tylko ważne fragmenty z pliku 3MB
        context_data = get_context(user_query)
        
        # 2. Zapytaj najnowszy model Gemini 2.0 Flash
        answer = await ask_gemini_2_0(user_query, context_data)
        
        # 3. Odpowiedz na grupie
        await msg.reply_text(answer)

def main():
    # Start serwera Flask w osobnym wątku (wymagane przez Koyeb)
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    
    # Inicjalizacja bota Telegram
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    
    print("Bot Gemini 2.0 Flash wystartował...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

