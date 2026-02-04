import os
import asyncio
import json
import re
import base64
import requests
import io
import struct
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- KONFIGURACJA ŚRODOWISKA (Fix dla Koyeb) ---
import telegram.ext
class DummyJobQueue:
    def __init__(self, *args, **kwargs): pass
    def set_application(self, application): pass
    async def start(self): pass
    async def stop(self): pass
telegram.ext.JobQueue = DummyJobQueue

# =========================
# USTAWIENIA
# =========================
API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
VOICE_NAME = "Orus" # Wybrany głos dla Orbita

# Pamięć krótkotrwała (ostatnie wiadomości z grupy)
CHAT_MEMORIES = {}
MAX_MEMORY_SIZE = 500

# Wczytywanie bazy wiedzy historycznej
KNOWLEDGE_LINES = []
if os.path.exists("knowledge.txt"):
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            KNOWLEDGE_LINES = [l.strip() for l in f.readlines() if l.strip()]
        print(f"INFO: Załadowano {len(KNOWLEDGE_LINES)} linii wiedzy historycznej.")
    except Exception as e:
        print(f"BŁĄD knowledge.txt: {e}")

# =========================
# POMOCNIKI
# =========================

def get_static_context(query, max_chars=8000):
    """Przeszukuje plik knowledge.txt pod kątem słów kluczowych (RAG)."""
    if not query: return ""
    keywords = re.findall(r'\b\w{4,}\b', query.lower())
    if not keywords: return "\n".join(KNOWLEDGE_LINES[-40:])
    
    matches = []
    current_len = 0
    for line in reversed(KNOWLEDGE_LINES):
        if any(kw in line.lower() for kw in keywords):
            matches.append(line)
            current_len += len(line)
            if current_len > max_chars: break
    return "\n".join(reversed(matches))

def pcm_to_wav(pcm_data, sample_rate=24000):
    """Konwertuje surowe dane PCM16 na format WAV."""
    num_channels = 1
    sample_width = 2
    with io.BytesIO() as wav_buf:
        wav_buf.write(b'RIFF')
        wav_buf.write(struct.pack('<I', 36 + len(pcm_data)))
        wav_buf.write(b'WAVEfmt ')
        wav_buf.write(struct.pack('<I', 16))
        wav_buf.write(struct.pack('<HHIIHH', 1, num_channels, sample_rate, sample_rate * num_channels * sample_width, num_channels * sample_width, sample_width * 8))
        wav_buf.write(b'data')
        wav_buf.write(struct.pack('<I', len(pcm_data)))
        wav_buf.write(pcm_data)
        return wav_buf.getvalue()

async def api_call(url, payload):
    """Wysyła zapytanie do Google API z obsługą retry i backoffu."""
    for i in range(5):
        try:
            res = requests.post(url, json=payload, timeout=60)
            if res.status_code == 200:
                return res.json()
            elif res.status_code == 429:
                await asyncio.sleep(2 ** i)
            else:
                print(f"API Error {res.status_code}: {res.text}")
                break
        except Exception as e:
            print(f"Request Exception: {e}")
            await asyncio.sleep(2 ** i)
    return None

# =========================
# FUNKCJE AI
# =========================

async def text_to_speech(text):
    """Zamienia tekst na głos AI."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": f"Say in a cool tone: {text}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": { "voiceConfig": { "voiceName": VOICE_NAME } }
        }
    }
    result = await api_call(url, payload)
    if result:
        try:
            audio_part = result['candidates'][0]['content']['parts'][0]['inlineData']
            pcm_bytes = base64.b64decode(audio_part['data'])
            rate = 24000
            rate_match = re.search(r'rate=(\d+)', audio_part['mimeType'])
            if rate_match: rate = int(rate_match.group(1))
            return pcm_to_wav(pcm_bytes, rate)
        except: pass
    return None

async def handle_gpt(update: Update, text_command: str, image_b64: str = None):
    """Obsługa czatu, wizji i pamięci."""
    chat_id = update.effective_chat.id
    query = text_command.replace('/gpt', '', 1).strip()
    
    # Pobierz pamięć i kontekst
    recent_chat = "\n".join(CHAT_MEMORIES.get(chat_id, []))
    static_data = get_static_context(query)
    
    sys_prompt = (
        "Jesteś wyluzowanym asystentem na grupie Telegram. Masz szorstki styl, "
        "używasz 'kurwa', ale nie obrażaj rozmówcy i nie nazywaj go debilem. "
        "Odpowiadaj krótko i wyłącznie po polsku.\n\n"
        "BIEŻĄCY CZAT (co pisali przed chwilą):\n"
        f"{recent_chat}\n\n"
        "WIEDZA O GRZE (z logów):\n"
        f"{static_data}\n\n"
        "ZASADA: Jesteś modelem Gemini, więc znasz się na wszystkim. "
        "Jeśli pytanie dotyczy gry, użyj logów. Jeśli świata, użyj wiedzy ogólnej. "
        "Jeśli czegoś nie wiesz, powiedz 'nie wiem kurwa'."
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
    parts = [{"text": query if query else "Co tam u was?"}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    payload = {
        "contents": [{"parts": parts}],
        "systemInstruction": {"parts": [{"text": sys_prompt}]}
    }

    result = await api_call(url, payload)
    if result:
        try:
            answer = result['candidates'][0]['content']['parts'][0]['text']
            await update.message.reply_text(answer)
            # Głos
            voice = await text_to_speech(answer)
            if voice:
                await update.message.reply_voice(voice=io.BytesIO(voice))
        except:
            await update.message.reply_text("AI coś przymuliło i nie dało tekstu.")
    else:
        await update.message.reply_text("Nie udało się połączyć z mózgiem AI.")

async def handle_img(update: Update, text: str):
    """Obsługa generowania obrazów Imagen 4.0."""
    prompt = text.replace('/img', '', 1).strip()
    if not prompt:
        return await update.message.reply_text("Napisz co rysować, kurwa.")
    
    wait = await update.message.reply_text("Rysuję to, sekunda...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict?key={API_KEY}"
    payload = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}}
    
    result = await api_call(url, payload)
    if result and 'predictions' in result:
        try:
            img_b64 = result['predictions'][0].get('bytesBase64Encoded')
            if img_b64:
                await update.message.reply_photo(photo=io.BytesIO(base64.b64decode(img_b64)))
                await wait.delete()
                return
        except: pass
    await wait.edit_text("Kurwa, nie udało się narysować tego obrazka.")

# =========================
# GŁÓWNA LOGIKA
# =========================

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS: return

    chat_id = update.effective_chat.id
    user_name = msg.from_user.full_name or "Anonim"
    text = msg.text or msg.caption or ""

    # Zapisz do pamięci bieżącej (jeśli to nie komenda)
    if chat_id not in CHAT_MEMORIES: CHAT_MEMORIES[chat_id] = []
    if text and not text.startswith('/'):
        clean_msg = f"{user_name}: {text}"
        CHAT_MEMORIES[chat_id].append(clean_msg)
        if len(CHAT_MEMORIES[chat_id]) > MAX_MEMORY_SIZE:
            CHAT_MEMORIES[chat_id].pop(0)

    # Przygotuj obrazek jeśli jest
    image_b64 = None
    if msg.photo:
        p = await msg.photo[-1].get_file()
        buf = io.BytesIO()
        await p.download_to_memory(buf)
        image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

    # Wykrywanie komend
    if text.lower().startswith('/gpt'):
        await handle_gpt(update, text, image_b64)
    elif text.lower().startswith('/img'):
        await handle_img(update, text)

# =========================
# SERWER I START
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is Online!", 200

def main():
    # Flask w tle
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    # Telegram
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot ruszył...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot gotowy do akcji.")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()




