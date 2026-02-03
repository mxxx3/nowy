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
API_KEY = os.environ.get("GEMINI_API_KEY") # Upewnij się, że ta zmienna jest w Koyeb
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]

# Wczytywanie bazy wiedzy
KNOWLEDGE_LINES = []
if os.path.exists("knowledge.txt"):
    with open("knowledge.txt", "r", encoding="utf-8") as f:
        KNOWLEDGE_LINES = [l.strip() for l in f.readlines() if l.strip()]
    print(f"Załadowano {len(KNOWLEDGE_LINES)} linii wiedzy.")

def get_context(query, max_chars=12000):
    if not query: return ""
    keywords = re.findall(r'\b\w{4,}\b', query.lower())
    if not keywords: return "\n".join(KNOWLEDGE_LINES[-60:])
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
    for i in range(5): # 5 prób (1s, 2s, 4s, 8s, 16s)
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
# HANDLER /GPT (CZAT + WIZJA)
# =========================
async def handle_gpt(update: Update, text: str, image_b64: str = None):
    query = text.replace('/gpt', '', 1).strip()
    context_data = get_context(query)
    
    sys_instruction = (
        "Jesteś wyluzowanym asystentem na grupie Telegram. Masz szorstki styl, "
        "możesz rzucić kurwą, ale NIE obrażaj użytkownika i NIE nazywaj go debilem. "
        "Odpowiadaj krótko i wyłącznie po polsku."
        "powiedz szczerze, że nie wiesz. Nie zmyślaj.\n\n"
        f"OTO LOGI GRY:\n{context_data}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
    
    parts = [{"text": query if query else "Analizuj."}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    payload = {
        "contents": [{"parts": parts}],
        "systemInstruction": {"parts": [{"text": sys_instruction}]}
    }

    result = await api_request(url, payload)
    if result:
        answer = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', "Kurwa, AI milczy.")
        await update.message.reply_text(answer)
    else:
        await update.message.reply_text("Coś się zjebało z połączeniem do czatu.")

# =========================
# HANDLER /IMG (TWORZENIE)
# =========================
async def handle_img(update: Update, text: str):
    prompt = text.replace('/img', '', 1).strip()
    if not prompt:
        await update.message.reply_text("Napisz co mam narysować po /img.")
        return

    msg = await update.message.reply_text("Rzeźbię to, czekaj chwilę...")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict?key={API_KEY}"
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {"sampleCount": 1}
    }

    result = await api_request(url, payload)
    if result and 'predictions' in result:
        img_data = result['predictions'][0].get('bytesBase64Encoded')
        if img_data:
            img_bytes = base64.b64decode(img_data)
            await update.message.reply_photo(photo=io.BytesIO(img_bytes))
            await msg.delete()
            return

    await msg.edit_text("Kurwa, nie udało się wygenerować obrazka. Może prompt był zbyt ostry?")

# =========================
# GŁÓWNA LOGIKA TELEGRAMA
# =========================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS: return

    text = msg.text or msg.caption or ""
    
    # Obsługa obrazka wysłanego do bota
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
def home(): return "Bot GPT/IMG is alive!", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot gotowy. /gpt do czatu, /img do rysowania.")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
