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
    """Wyciąga tekst z pojedynczego pliku JSON Telegrama."""
    if not os.path.exists(file_path):
        print(f"INFO: Plik {file_path} nie istnieje. Pomijam.")
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
                
                # Telegram JSON może mieć tekst jako string lub listę obiektów
                if isinstance(text_field, list):
                    clean_text = "".join([part["text"] if isinstance(part, dict) else str(part) for part in text_field])
                else:
                    clean_text = str(text_field)
                
                extracted.append(f"{sender}: {clean_text}")
        
        return "\n".join(extracted)
    except Exception as e:
        print(f"BŁĄD przy {file_path}: {e}")
        return ""

# Wczytujemy dane z obu plików
print("Ładowanie bazy wiedzy...")
KNOWLEDGE_1 = parse_telegram_json("result.json")
KNOWLEDGE_2 = parse_telegram_json("result1.json")
FULL_KNOWLEDGE = KNOWLEDGE_1 + "\n\n--- DANE Z DRUGIEJ GRUPY ---\n\n" + KNOWLEDGE_2

# --- CHARAKTER BOTA (SYSTEM PROMPT) ---
SYSTEM_PROMPT = f"""
Jesteś wyluzowanym asystentem na grupie Telegram. 
Masz specyficzny, nieco szorstki styl bycia – możesz czasem przekląć, 
ale nie obrażaj użytkownika i nie nazywaj go debilem.

TWOJA WIEDZA O GRZE (Dane z dwóch grup, mogą być w różnych językach):
{FULL_KNOWLEDGE[:900000]} 

ZASADY: 
1. ODPOWIADAJ ZAWSZE I WYŁĄCZNIE PO POLSKU. Nawet jeśli informacja w bazie wiedzy jest po angielsku lub w innym języku, przetłumacz ją w głowie i odpisz po polsku.
2. Jeśli użytkownik pyta o mechaniki gry, plany lub historię z grup, szukaj odpowiedzi w powyższych danych.
3. Jeśli w danych nie ma odpowiedzi i Ty też jej nie znasz, powiedz szczerze: "Nie wiem kurwa, nikt o tym nie pisał w tych logach". 
4. Nigdy nie zmyślaj faktów o grze. Jeśli nie masz dowodów - nie wiesz.
"""

# =========================
# INICJALIZACJA AI
# =========================
genai.configure(api_key=GEMINI_KEY)
# Używamy modelu Flash 1.5, który świetnie radzi sobie z dużym kontekstem
model = genai.GenerativeModel(model_name="gemini-1.5-flash", system_instruction=SYSTEM_PROMPT)

# =========================
# SERWER WWW i LOGIKA
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is running with double JSON knowledge!", 200

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    if update.effective_chat.id not in ALLOWED_GROUPS: return

    if msg.text.lower().startswith('/gpt'):
        prompt = msg.text.replace('/gpt', '', 1).strip()
        query = prompt if prompt else "Przejrzyj dane i powiedz, co ciekawego ustalili gracze w obu grupach."

        try:
            # Generowanie odpowiedzi (Gemini automatycznie przetłumaczy wiedzę na polski wg instrukcji)
            response = model.generate_content(query)
            if response and response.text:
                await msg.reply_text(response.text)
            else:
                await msg.reply_text("Kurwa, AI nic nie odpowiedziało. Może znowu filtry.")
        except Exception as e:
            print(f"Błąd AI: {e}")
            await msg.reply_text("Coś się zjebało przy łączeniu z mózgiem bota.")

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

