import os
import asyncio
import json
import re
import base64
import requests
import io
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- KONFIGURACJA KOYEB ---
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
API_KEY = os.environ.get("GEMINI_API_KEY") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]

# Wczytywanie bazy wiedzy gry
KNOWLEDGE_LINES = []
if os.path.exists("knowledge.txt"):
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            KNOWLEDGE_LINES = [l.strip() for l in f.readlines() if l.strip()]
        print(f"Załadowano {len(KNOWLEDGE_LINES)} linii wiedzy z pliku.")
    except Exception as e:
        print(f"Błąd wczytywania pliku: {e}")

def get_game_context(query, max_chars=12000):
    if not query: return ""
    keywords = re.findall(r'\b\w{4,}\b', query.lower())
    if not keywords: return "\n".join(KNOWLEDGE_LINES[-50:])
    matches = []
    current_len = 0
    for line in reversed(KNOWLEDGE_LINES):
        if any(kw in line.lower() for kw in keywords):
            matches.append(line)
            current_len += len(line)
            if current_len > max_chars: break
    return "\n".join(reversed(matches))

# =========================
# FUNKCJE API Z BACKOFFEM
# =========================
async def api_request(url, payload):
    for i in range(5):
        try:
            res = requests.post(url, json=payload, timeout=60)
            if res.status_code == 200:
                return res.json()
            elif res.status_code == 429:
                await asyncio.sleep(2 ** i)
            else:
                print(f"Błąd API {res.status_code}: {res.text}")
                break
        except Exception as e:
            print(f"Wyjątek API: {e}")
            await asyncio.sleep(2 ** i)
    return None

# =========================
# HANDLER /GPT (CZAT + WIEDZA OGÓLNA + WIZJA)
# =========================
async def handle_gpt(update: Update, text: str, image_b64: str = None):
    query = text.replace('/gpt', '', 1).strip()
    game_context = get_game_context(query)
    
    sys_instruction = (
        "Jesteś wyluzowanym asystentem na grupie Telegram. Masz szorstki styl, "
        "możesz przekląć (używaj 'kurwa'), ale NIE obrażaj użytkownika i NIE nazywaj go debilem. "
        "Odpowiadaj krótko, zwięźle i wyłącznie po polsku.\n\n"
        "MASZ DWIE MOŻLIWOŚCI ODPOWIEDZI:\n"
        "1. Korzystaj ze swojej ogólnej wiedzy jako model AI (odpowiadaj na pytania o świecie, nauce, życiu).\n"
        "2. Jeśli pytanie dotyczy konkretnie gry lub wydarzeń na grupie, korzystaj z poniższych logów.\n\n"
        f"LOGI GRY DLA KONTEKSTU:\n{game_context}\n\n"
        "ZASADA: Jeśli czegoś naprawdę nie wiesz (ani z logów, ani z wiedzy ogólnej), powiedz 'Nie wiem'. "
        "Nigdy nie zmyślaj faktów."
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
    
    parts = [{"text": query if query else "Co tam?"}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    payload = {
        "contents": [{"parts": parts}],
        "systemInstruction": {"parts": [{"text": sys_instruction}]}
    }

    result = await api_request(url, payload)
    if result:
        try:
            answer = result['candidates'][0]['content']['parts'][0]['text']
            await update.message.reply_text(answer)
        except (KeyError, IndexError):
            await update.message.reply_text("Kurwa, AI coś zacięło i nie wypluło tekstu.")
    else:
        await update.message.reply_text("Nie udało się połączyć z mózgiem AI.")

# =========================
# HANDLER /IMG (GENERATOR IMAGEN 4.0)
# =========================
async def handle_img(update: Update, text: str):
    prompt = text.replace('/img', '', 1).strip()
    if not prompt:
        await update.message.reply_text("Napisz co mam narysować, kurwa.")
        return

    msg = await update.message.reply_text("Rysuję, daj mi chwilę...")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict?key={API_KEY}"
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {"sampleCount": 1}
    }

    result = await api_request(url, payload)
    if result and 'predictions' in result:
        try:
            img_data = result['predictions'][0].get('bytesBase64Encoded')
            if img_data:
                img_bytes = base64.b64decode(img_data)
                await update.message.reply_photo(photo=io.BytesIO(img_bytes))
                await msg.delete()
                return
        except: pass

    await msg.edit_text("Kurwa, Imagen nie chciał tego narysować. Spróbuj zmienić opis.")

# =========================
# GŁÓWNA LOGIKA
# =========================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS: return

    text = msg.text or msg.caption or ""
    
    image_b64 = None
    if msg.photo:
        photo_file = await msg.photo[-1].get_file()
        buf = io.BytesIO()
        await photo_file.download_to_memory(buf)
        image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

    if text.lower().startswith('/gpt'):
        await handle_gpt(update, text, image_b64)
    elif text.lower().startswith('/img'):
        await handle_img(update, text)

app = Flask(__name__)
@app.route("/")
def home(): return "All-in-One Bot is Live!", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot gotowy do akcji.")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
