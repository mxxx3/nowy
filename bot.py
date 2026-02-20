import os
import asyncio
import json
import base64
import httpx  # Używamy httpx do zapytań równoległych
import io
import struct
import random
import time
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# --- KONFIGURACJA ŚRODOWISKA (Koyeb Fix) ---
import telegram.ext
class DummyJobQueue:
    def __init__(self, *args, **kwargs): pass
    def set_application(self, application): pass
    async def start(self): pass
    async def stop(self): pass
telegram.ext.JobQueue = DummyJobQueue

# =========================
# KONFIGURACJA MODELI 2026
# =========================
MODELS_TO_TRY = [
    "gemini-2.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.0-flash",
    "gemini-1.5-flash"
]

API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]

# Twoja ekipa (Ziomki)
NASI_ZIOMKI = "Gal, Karol, Nassar, Łukasz, DonMacias, Polski Ninja, Oliv, One Way Ticket, Bajtkojn, Tomek, Mando, mateusz, Pdablju, XDemon, Michal K, SHARK, KrisFX, Halison, Wariat95, Shadows, andzia, Marzena, Kornello, Tomasz, DonMakveli, Lucifer, Stara Janina, Matis64, Kama, Kicia, Kociamber Auuu, KERTH, Ulalala, Dorcia, Kuba, Damian, Marshmallow, KarolCarlos, PIRATEPpkas Pkas, Maniek, HuntFiWariat9501, Krystiano1993, Jazda jazda, Dottie, Khent"

VOICE_NAME = "Despina"

# =========================
# NARZĘDZIA AUDIO
# =========================

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

# =========================
# LOGIKA RÓWNOLEGŁEGO WYWOŁANIA
# =========================

async def fetch_from_model(client, model_name, payload):
    """Pojedyncze asynchroniczne zapytanie do konkretnego modelu."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    try:
        response = await client.post(url, json=payload, timeout=30.0)
        if response.status_code == 200:
            return model_name, response.json()
    except Exception as e:
        print(f"DEBUG: Model {model_name} wywalił błąd: {e}")
    return model_name, None

async def run_karyna_parallel(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    sys_instruction = (
        "Jesteś Karyną. Pyskata, wyluzowana dziewczyna z polskiego osiedla. "
        "Nie jesteś AI, tylko człowiekiem. Mówisz szorstko, potocznie, po polsku. "
        "Jeśli czegoś nie wiesz, mów szczerze: 'nie wiem kurwa, nie było mnie tam'. "
        "NASZA EKIPA (ZIOMKI): " + NASI_ZIOMKI + ". "
        "Analizuj raporty walk na screenach. Jeśli nasi przegrali, pociesz ich. Jak wygrali, chwal."
    )

    parts = [{"text": prompt if prompt else "Co tam u ziomków?"}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/png", "data": image_b64}})

    payload = {
        "contents": [{"parts": parts}],
        "systemInstruction": {"parts": [{"text": sys_instruction}]},
        "generationConfig": {
            "responseModalities": ["TEXT", "AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": { "voiceName": VOICE_NAME }
                }
            }
        }
    }

    # Wysyłamy zapytania do wszystkich modeli jednocześnie
    async with httpx.AsyncClient() as client:
        tasks = [fetch_from_model(client, m, payload) for m in MODELS_TO_TRY]
        
        # Czekamy na pierwszy sukces
        for completed_task in asyncio.as_completed(tasks):
            model_name, result = await completed_task
            
            if result:
                # Mamy zwycięzcę! Wyciągamy dane.
                try:
                    candidate_parts = result['candidates'][0]['content']['parts']
                    ans_text = ""
                    audio_b64 = ""
                    
                    for part in candidate_parts:
                        if 'text' in part: ans_text = part['text']
                        if 'inlineData' in part: audio_b64 = part['inlineData']['data']

                    if ans_text:
                        await update.message.reply_text(f"{ans_text}\n\n⚡️ Odpalił: {model_name}")
                    
                    if audio_b64:
                        wav_data = pcm_to_wav(base64.b64decode(audio_b64))
                        await update.message.reply_audio(
                            audio=io.BytesIO(wav_data), 
                            filename="karyna.wav", 
                            title=f"Karyna mówi ({model_name})"
                        )
                    return # Kończymy funkcję, bo już odpowiedzieliśmy
                except:
                    continue # Jeśli dane były wybrakowane, sprawdzamy następny zakończony task

    await update.message.reply_text("Kurwa, żaden model nie odpowiedział. Sprawdź neta albo klucz API.")

# =========================
# HANDLERY
# =========================

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ID grupy: `{update.effective_chat.id}`\nTryb: Super Parallel (No Firebase)")

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id not in ALLOWED_GROUPS: return

    text = msg.text or msg.caption or ""
    image_b64 = None

    if msg.photo:
        try:
            p = await msg.photo[-1].get_file()
            buf = io.BytesIO()
            await p.download_to_memory(buf)
            image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        except: pass

    if "karyna" in text.lower():
        await run_karyna_parallel(update, context, text, image_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna Parallel Mode Active", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("id", get_id))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot Karyna (Parallel Mode) ruszył!")
    application.run_polling()

if __name__ == "__main__":
    main()
