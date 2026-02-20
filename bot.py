import os
import asyncio
import json
import base64
import requests
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
# KONFIGURACJA MODELI
# =========================
MODELS_TO_TRY = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite"
]

API_KEY = os.environ.get("GEMINI_API_KEY", "") 
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_GROUPS = [-1003676480681, -1002159478145]

# Twoja ekipa (Ziomki) - dołączone bezpośrednio do instrukcji
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
# GŁÓWNA LOGIKA KARYNY
# =========================

async def run_karyna_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_b64: str = None):
    chat_id = update.effective_chat.id
    
    # Status na Telegramie
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    sys_instruction = (
        "Jesteś Karyną. Pyskata, wyluzowana dziewczyna z polskiego osiedla. "
        "Nie jesteś AI, jesteś żywym człowiekiem. Mówisz szorstko, potocznie, po polsku. "
        "Jeśli czegoś nie wiesz, powiedz szczerze: 'nie wiem kurwa, nie było mnie tam'. "
        "Nie zmyślaj informacji.\n\n"
        f"NASZA EKIPA (ZIOMKI): {NASI_ZIOMKI}.\n"
        "Analizuj raporty z gier na zdjęciach. Jeśli nasi przegrali, pociesz ich. "
        "Jak wygrali, chwal ich. Nie kłam, że wygraliśmy, jeśli widać porażkę."
    )

    parts = [{"text": prompt if prompt else "Co tam?"}]
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

    success = False
    for model_name in MODELS_TO_TRY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
        try:
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                data = res.json()
                candidate_parts = data['candidates'][0]['content']['parts']
                
                ans_text = ""
                audio_b64 = ""
                
                for part in candidate_parts:
                    if 'text' in part:
                        ans_text = part['text']
                    if 'inlineData' in part:
                        audio_b64 = part['inlineData']['data']

                if ans_text:
                    await update.message.reply_text(ans_text)
                
                if audio_b64:
                    wav_data = pcm_to_wav(base64.b64decode(audio_base64 := audio_b64))
                    await update.message.reply_audio(
                        audio=io.BytesIO(wav_data), 
                        filename="karyna.wav", 
                        title=f"Karyna ({model_name})"
                    )
                
                success = True
                break
            else:
                print(f"DEBUG: {model_name} rzucił błąd {res.status_code}")
        except Exception as e:
            print(f"DEBUG: Wyjątek dla {model_name}: {e}")
            continue

    if not success:
        await update.message.reply_text("Kurwa, wszystkie modele Google mają zacięcie. Sprawdź klucz API.")

# =========================
# HANDLERY
# =========================

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ID grupy: `{update.effective_chat.id}`\nBaza: WYŁĄCZONA (Speed Mode)")

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

    # Reaguje TYLKO na słowo "karyna"
    if "karyna" in text.lower():
        await run_karyna_logic(update, context, text, image_b64)

app = Flask(__name__)
@app.route("/")
def home(): return "Karyna Speed Mode", 200

def main():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    application = ApplicationBuilder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("id", get_id))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    print("Bot Karyna (Speed Mode) ruszył!")
    application.run_polling()

if __name__ == "__main__":
    main()
