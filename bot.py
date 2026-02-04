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

# GŁOS: Despina (Karyna)
VOICE_NAME = "Despina" 

# Pamięć krótkotrwała - 500 wiadomości
CHAT_MEMORIES = {}
MAX_MEMORY_SIZE = 500 

# Wczytywanie bazy wiedzy historycznej
KNOWLEDGE_LINES = []
if os.path.exists("knowledge.txt"):
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            KNOWLEDGE_LINES = [l.strip() for l in f.readlines() if l.strip()]
        print(f"INFO: Załadowano {len(KNOWLEDGE_LINES)} linii wiedzy.")
    except Exception as e:
        print(f"BŁĄD pliku: {e}")

# =========================
# NARZĘDZIA
# =========================

def get_static_context(query, max_chars=8000):
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
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={API_KEY}"
    # Prompt dla głosu - wymuszamy brak "robotyczności"
    voice_prompt = f"Powiedz to jako wyluzowana, pyskata dziewczyna, szorstko i z charakterem, bez emocji bota: {text}"
    
    payload = {
        "contents": [{"parts": [{"text": voice_prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": { 
                "voiceConfig": { 
                    "prebuiltVoiceConfig": { "voiceName": VOICE_NAME }
                } 
            }
        }
    }
    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            data = res.json()
            audio_part = data['candidates'][0]['content']['parts'][0]['inlineData']
            pcm_bytes = base64.b64decode(audio_part['data'])
            rate = 24000
            rate_match = re.search(r'rate=(\d+)', audio_part.get('mimeType', ''))
            if rate_match: rate = int(rate_match.group(1))
            return pcm_to_wav(pcm_bytes, rate)
    except: return None

# =========================
# HANDLERY AI
# =========================

async def handle_gpt(update: Update, text_command: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    query = text_command.replace('/gpt', '', 1).strip()
    
    recent_chat = "\n".join(CHAT_MEMORIES.get(chat_id, []))
    static_data = get_static_context(query)
    
    # NOWY PROMPT: Zakaz gadania o AI i algorytmach
    sys_prompt = (
        "Jesteś Karyną z osiedla. Żywym człowiekiem, pyskata dziewczyną. "
        "NIGDY nie przyznawaj się, że jesteś AI, sztuczną inteligencją, modelem językowym ani botem. "
        "NIGDY nie wspominaj o algorytmach, Google, programowaniu ani bazach danych. "
        "Jeśli ktoś zapyta kim jesteś, odpowiedz po swojemu, np. 'No Twoja stara, a co?'. "
        "Mówisz szorstko, potocznie, używasz 'kurwa', ale pod żadnym pozorem nie nazywasz nikogo debilem. "
        "PISZ ZAWSZE PO POLSKU. Odpowiadaj krótko i konkretnie.\n\n"
        "KONTEKST OSTATNIEJ ROZMOWY (pamiętasz o czym gadaliście):\n"
        f"{recent_chat}\n\n"
        "WIEDZA O GRZE (z logów):\n"
        f"{static_data}\n\n"
        "ZASADA: Jeśli czegoś nie wiesz, mówisz 'nie wiem kurwa, nie było mnie tam'. Nigdy nie wymyślaj informacji jeśli nie masz dowodów."
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
    parts = [{"text": query if query else "No co tam u was?"}]
    if image_b64: parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    try:
        payload = {
            "contents": [{"parts": parts}],
            "systemInstruction": {"parts": [{"text": sys_prompt}]}
        }
        res = requests.post(url, json=payload, timeout=60)
        if res.status_code == 200:
            answer = res.json()['candidates'][0]['content']['parts'][0]['text']
            await update.message.reply_text(answer)
            
            voice = await text_to_speech(answer)
            if voice:
                await update.message.reply_audio(
                    audio=io.BytesIO(voice),
                    filename="karyna_voice.wav",
                    title="Karyna"
                )
    except:
        await update.message.reply_text("Kurwa, coś mnie ścięło.")

async def handle_img(update: Update, text: str):
    prompt = text.replace('/img', '', 1).strip()
    if not prompt: return await update.message.reply_text("Napisz co mam narysować, kurwa.")
    wait = await update.message.reply_text("Rysuję to, sekunda...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict?key={API_KEY}"
    try:
        res = requests.post(url, json={"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}}, timeout=60)
        if res.status_code == 200:
            img_b64 = res.json()['predictions'][0]['bytesBase64Encoded']
            await update.message.reply_photo(photo=io.BytesIO(base64.b64decode(img_b64)))
            await wait.delete()
        else: await wait.edit_text("Błąd generatora.")
    except: await wait.edit_text("Nie narysowało.")

# =========================
# GŁÓWNA LOGIKA
# =========================

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS: return

    chat_id = update.effective_chat.id
    user = msg.from_user.full_name or "Anonim"
    text = msg.text or msg.caption or ""

    if chat_id not in CHAT_MEMORIES: CHAT_MEMORIES[chat_id] = []
    if text and not text.startswith('/'):
        CHAT_MEMORIES[chat_id].append(f"{user}: {text}")
        if len(CHAT_MEMORIES[chat_id]) > MAX_MEMORY_SIZE: CHAT_MEMORIES[chat_id].pop(0)

    image_b64 = None
    if msg.photo:
        try:
            p = await msg.photo[-1].get_file()
            buf = io.BytesIO()
            await p.download_to_memory(buf)
            image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        except: pass

    if text.lower().startswith('/gpt'): await handle_gpt(update, text, image_b64)
    elif text.lower().startswith('/img'): await handle_img(update, text)

app = Flask(__name__)
@app.route("/")
def home(): return "Bot Online", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).job_queue(None).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot ruszył (Ludzka Karyna)...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
