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
# KONFIGURACJA
# =========================
API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]
VOICE_NAME = "Orus" # Głos najbardziej zbliżony do Orbita

# Pamięć krótkotrwała (ostatnie wiadomości z grupy)
CHAT_MEMORIES = {}
MAX_MEMORY_SIZE = 500

# Wczytywanie bazy wiedzy historycznej (knowledge.txt)
KNOWLEDGE_LINES = []
if os.path.exists("knowledge.txt"):
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            KNOWLEDGE_LINES = [l.strip() for l in f.readlines() if l.strip()]
        print(f"INFO: Załadowano {len(KNOWLEDGE_LINES)} linii wiedzy historycznej.")
    except Exception as e:
        print(f"BŁĄD wczytywania knowledge.txt: {e}")

def get_static_context(query, max_chars=8000):
    """Przeszukuje plik knowledge.txt pod kątem słów kluczowych."""
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

# =========================
# NARZĘDZIA AUDIO (TTS)
# =========================
def pcm_to_wav(pcm_data, sample_rate=24000):
    """Konwertuje surowe dane PCM na format WAV dla Telegrama."""
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

async def text_to_speech(text):
    """Generuje głos AI."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": f"Say in a cool, slightly rough tone: {text}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": { "voiceConfig": { "voiceName": VOICE_NAME } }
        }
    }
    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            data = res.json()
            audio_part = data['candidates'][0]['content']['parts'][0]['inlineData']
            pcm_bytes = base64.b64decode(audio_part['data'])
            rate = 24000
            rate_match = re.search(r'rate=(\d+)', audio_part['mimeType'])
            if rate_match: rate = int(rate_match.group(1))
            return pcm_to_wav(pcm_bytes, rate)
    except Exception as e:
        print(f"TTS Error: {e}")
        return None

# =========================
# HANDLER /GPT (CZAT + WIDOK + PAMIĘĆ)
# =========================
async def handle_gpt(update: Update, text_command: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    query = text_command.replace('/gpt', '', 1).strip()
    
    # Pobierz pamięć bieżącą i historyczną
    recent_chat = "\n".join(CHAT_MEMORIES.get(chat_id, []))
    static_data = get_static_context(query)
    
    sys_prompt = (
        "Jesteś wyluzowanym asystentem na grupie Telegram. Masz szorstki styl, "
        "używasz 'kurwa', ale nie obrażaj rozmówcy i nie nazywaj go debilem. "
        "Odpowiadaj krótko i po polsku.\n\n"
        "BIEŻĄCY CZAT (to co pisaliście przed chwilą):\n"
        f"{recent_chat}\n\n"
        "WIEDZA O GRZE (z logów historycznych):\n"
        f"{static_data}\n\n"
        "ZASADA: Jeśli pytanie dotyczy świata, użyj wiedzy ogólnej. "
        "Jeśli dotyczy gry lub kogoś z czatu, użyj powyższych danych. "
        "Masz dostęp do wizji, jeśli przesłano obraz."
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
    parts = [{"text": query if query else "Analizuj co się dzieje."}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    payload = {
        "contents": [{"parts": parts}],
        "systemInstruction": {"parts": [{"text": sys_prompt}]}
    }

    try:
        res = requests.post(url, json=payload, timeout=60)
        if res.status_code == 200:
            answer = res.json()['candidates'][0]['content']['parts'][0]['text']
            await update.message.reply_text(answer)
            
            # Generuj i wyślij głos
            voice = await text_to_speech(answer)
            if voice:
                await update.message.reply_voice(voice=io.BytesIO(voice))
        else:
            print(f"API Error: {res.text}")
            await update.message.reply_text("Kurwa, błąd API. Sprawdź klucz.")
    except Exception as e:
        print(f"Request Error: {e}")
        await update.message.reply_text("Nie udało się połączyć z AI.")

# =========================
# HANDLER /IMG (GENERATOR)
# =========================
async def handle_img(update: Update, text: str):
    prompt = text.replace('/img', '', 1).strip()
    if not prompt:
        return await update.message.reply_text("Napisz co rysować, kurwa.")
    
    wait = await update.message.reply_text("Rzeźbię to, czekaj...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict?key={API_KEY}"
    
    try:
        payload = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}}
        res = requests.post(url, json=payload, timeout=60)
        if res.status_code == 200:
            img_b64 = res.json()['predictions'][0]['bytesBase64Encoded']
            await update.message.reply_photo(photo=io.BytesIO(base64.b64decode(img_b64)))
            await wait.delete()
        else:
            await wait.edit_text("Kurwa, błąd generatora.")
    except:
        await wait.edit_text("Coś się zjebało przy rysowaniu.")

# =========================
# GŁÓWNA LOGIKA
# =========================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS:
        return

    chat_id = update.effective_chat.id
    user_name = msg.from_user.full_name or "Anonim"
    text = msg.text or msg.caption or ""

    # ZAPISYWANIE DO PAMIĘCI (jeśli to nie komenda)
    if chat_id not in CHAT_MEMORIES:
        CHAT_MEMORIES[chat_id] = []
    
    if text and not text.startswith('/'):
        clean_msg = f"{user_name}: {text}"
        CHAT_MEMORIES[chat_id].append(clean_msg)
        if len(CHAT_MEMORIES[chat_id]) > MAX_MEMORY_SIZE:
            CHAT_MEMORIES[chat_id].pop(0)

    # OBSŁUGA KOMEND
    image_b64 = None
    if msg.photo:
        p = await msg.photo[-1].get_file()
        buf = io.BytesIO()
        await p.download_to_memory(buf)
        image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

    if text.lower().startswith('/gpt'):
        await handle_gpt(update, text, image_b64)
    elif text.lower().startswith('/img'):
        await handle_img(update, text)

app = Flask(__name__)
@app.route("/")
def home(): return "Bot is Online!", 200

def main():
    # Uruchom Flask w tle
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    
    # Uruchom Telegram
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    
    print("Bot gotowy do akcji.")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()[{"text": sys_instruction}]}
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


