import json
import queue
import re
from collections import deque
import shutil
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import requests
import sounddevice as sd
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from vosk import Model, KaldiRecognizer


# ============================================================
# ПУТИ И НАСТРОЙКИ
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "vosk-model-ru-0.42"

SAMPLE_RATE = 16000
BLOCK_SIZE = 4096
CHANNELS = 1

WAKE_PHRASE = "городовой"

API_HOST = "127.0.0.1"
API_PORT = 8000
API_URL = f"http://{API_HOST}:{API_PORT}/receive"

# Ограничения очередей 
AUDIO_QUEUE_SIZE = 100
AUDIO_RECORD_QUEUE_SIZE = 500
HISTORY_LIMIT = 500


# ============================================================
# ПАПКА ЛОГОВ ТЕКУЩЕГО ЗАПУСКА
# ============================================================

RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
LOG_DIR = BASE_DIR / "logs" / RUN_ID
COMMANDS_DIR = LOG_DIR / "commands"
LOG_DIR.mkdir(parents=True, exist_ok=True)
COMMANDS_DIR.mkdir(parents=True, exist_ok=True)

TRANSCRIPT_PATH = LOG_DIR / "transcript.txt"
WAV_PATH = LOG_DIR / "microphone.wav"
MP3_PATH = LOG_DIR / "microphone.mp3"

transcript_file = TRANSCRIPT_PATH.open("a", encoding="utf-8", buffering=1)
audio_file = wave.open(str(WAV_PATH), "wb")
audio_file.setnchannels(CHANNELS)
audio_file.setsampwidth(2)
audio_file.setframerate(SAMPLE_RATE)


# ============================================================
# СОСТОЯНИЕ И ОЧЕРЕДИ
# ============================================================

app = FastAPI(title="Распознавание речи")

latest_result = ""
history = []
data_lock = threading.Lock()

# В очередь отправляем один словарь
send_queue = queue.Queue()
audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_SIZE)
audio_record_queue = queue.Queue(maxsize=AUDIO_RECORD_QUEUE_SIZE)

audio_log_overflow_reported = False
audio_writer_thread = None
speech_thread = None
api_thread = None
api_server_thread = None

listening_for_command = False
command_audio_chunks = None
stop_event = threading.Event()


# ============================================================
# ВЕБ-СТРАНИЦА
# ============================================================

HTML_PAGE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Распознавание речи</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f2f2f2;
            margin: 40px;
            color: #222;
        }
        .box {
            background: white;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 8px #0000000d;
        }
        .latest {
            font-size: 28px;
            font-weight: bold;
            overflow-wrap: anywhere;
        }
        .item {
            padding: 10px 0;
            border-bottom: 1px solid #ddd;
            overflow-wrap: anywhere;
        }
        .time {
            color: #777;
            font-size: 13px;
            margin-top: 5px;
        }
    </style>
</head>
<body>
    <h1>Распознавание речи</h1>
    <div class="box">
        <div>Последняя распознанная команда:</div>
        <div class="latest" id="latest">—</div>
    </div>
    <div class="box">
        <h2>История</h2>
        <div id="history">Пока пусто</div>
    </div>
<script>
async function updateData() {
    try {
        const resultResponse = await fetch("/result");
        const result = await resultResponse.json();
        document.getElementById("latest").textContent = result.text || "—";

        const historyResponse = await fetch("/history");
        const items = await historyResponse.json();
        const historyElement = document.getElementById("history");
        historyElement.replaceChildren();

        if (items.length === 0) {
            historyElement.textContent = "Пока пусто";
            return;
        }

        items.slice().reverse().forEach(item => {
            const div = document.createElement("div");
            div.className = "item";

            const textDiv = document.createElement("div");
            textDiv.textContent = item.text;

            const timeDiv = document.createElement("div");
            timeDiv.className = "time";
            timeDiv.textContent = item.time;

            div.appendChild(textDiv);
            div.appendChild(timeDiv);
            historyElement.appendChild(div);
        });
    } catch (error) {
        console.log("Ошибка обновления:", error);
    }
}
setInterval(updateData, 500);
updateData();
</script>
</body>
</html>
"""


# ============================================================
# API ROUTES
# ============================================================

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/result")
def get_result():
    with data_lock:
        return {"text": latest_result}


@app.get("/history")
def get_history():
    with data_lock:
        return list(history)


@app.post("/receive")
def receive_result(data: dict):
    text = str(data.get("text", ""))
    received_time = str(data.get("time", ""))

    print()
    print("========================================")
    print("[ПОЛУЧЕНО ПО API]")
    print(f"Текст: {text}")
    print(f"Время: {received_time}")
    print("========================================")
    print()

    return {"status": "received"}


# ============================================================
# НОРМАЛИЗАЦИЯ И ПРОВЕРКА WAKE WORD
# ============================================================

def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    for char in ",.!?;:\"'()[]{}":
        text = text.replace(char, " ")
    return " ".join(text.split())


def contains_wake_phrase(text: str) -> bool:
    normalized = f" {normalize(text)} "
    phrase = f" {normalize(WAKE_PHRASE)} "
    return phrase in normalized


# ============================================================
# СОХРАНЕНИЕ РЕЗУЛЬТАТА
# ============================================================

def save_result(text: str, audio_chunks=None):
    global latest_result

    current_time = datetime.now().strftime("%H:%M:%S")
    log_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with data_lock:
        latest_result = text
        history.append({"text": text, "time": current_time})
        if len(history) > HISTORY_LIMIT:
            del history[:-HISTORY_LIMIT]

    transcript_file.write(f"[{log_time}] {text}\n")
    transcript_file.flush()

    # Один элемент-словарь
    send_queue.put({
        "text": text,
        "audio_chunks": list(audio_chunks) if audio_chunks else [],
    })


def save_audio_segment(audio_chunks):
    if not audio_chunks:
        return

    timestamp = datetime.now().strftime("%H%M%S_%f")
    wav_path = COMMANDS_DIR / f"{timestamp}.wav"
    mp3_path = COMMANDS_DIR / f"{timestamp}.mp3"

    try:
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(b"".join(audio_chunks))

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            subprocess.run(
                [
                    ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(wav_path),
                    "-codec:a", "libmp3lame", "-q:a", "4",
                    str(mp3_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            wav_path.unlink(missing_ok=True)
            print(f"[АУДИО КОМАНДЫ] Сохранено: {mp3_path}")
        else:
            print(f"[АУДИО КОМАНДЫ] FFmpeg не найден; сохранён WAV: {wav_path}")

    except Exception as exc:
        print(f"[ОШИБКА СОХРАНЕНИЯ АУДИО КОМАНДЫ] {exc}", file=sys.stderr)


# ============================================================
# ОТПРАВКА API В ОТДЕЛЬНОМ ПОТОКЕ
# ============================================================

def api_sender():
    while not stop_event.is_set() or not send_queue.empty():
        try:
            item = send_queue.get(timeout=0.2)
        except queue.Empty:
            continue

        try:
            if item is None:
                break

            text = item["text"]
            audio_chunks = item.get("audio_chunks", [])
            payload = {
                "text": text,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            try:
                response = requests.post(API_URL, json=payload, timeout=2)
                print(f"[API ОТПРАВКА] {response.status_code} → {text}")
            except requests.RequestException as exc:
                print(f"[ОШИБКА API] {exc}")

            save_audio_segment(audio_chunks)

        except Exception as exc:
            print(f"[ОШИБКА ПОТОКА ОТПРАВКИ] {exc}", file=sys.stderr)
        finally:
            send_queue.task_done()


# ============================================================
# ПОТОК ЗАПИСИ МИКРОФОНА В WAV
# ============================================================

def audio_writer():
    while True:
        audio = audio_record_queue.get()
        try:
            if audio is None:
                return
            audio_file.writeframesraw(audio)
        except Exception as exc:
            print(f"[ОШИБКА ЗАПИСИ МИКРОФОНА] {exc}", file=sys.stderr)
        finally:
            audio_record_queue.task_done()


# ============================================================
# CALLBACK МИКРОФОНА
# ============================================================

def microphone_callback(indata, frames, time_info, status):
    global audio_log_overflow_reported

    if status:
        print(f"[AUDIO] {status}", file=sys.stderr)

    audio = bytes(indata)

    try:
        audio_record_queue.put_nowait(audio)
    except queue.Full:
        if not audio_log_overflow_reported:
            print(
                "[AUDIO LOG] Очередь записи переполнена; часть аудио может потеряться.",
                file=sys.stderr,
            )
            audio_log_overflow_reported = True

    try:
        audio_queue.put_nowait(audio)
    except queue.Full:
        # Если распознавание отстаёт - выбрасываем старый блок
        try:
            audio_queue.get_nowait()
            audio_queue.task_done()
        except queue.Empty:
            pass
        try:
            audio_queue.put_nowait(audio)
        except queue.Full:
            pass


# ============================================================
# ПОТОК РАСПОЗНАВАНИЯ РЕЧИ
# ============================================================

def speech_worker(model):
    global listening_for_command, command_audio_chunks

    wake_recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    command_recognizer = KaldiRecognizer(model, SAMPLE_RATE)

    pre_roll = deque(maxlen=12)  # примерно 3 секунды при BLOCK_SIZE=4096

    try:
        wake_recognizer.SetEndpointerDelays(0.5, 0.3, 10.0)
        command_recognizer.SetEndpointerDelays(0.5, 0.3, 10.0)
    except (AttributeError, TypeError):
        pass

    print()
    print("========================================")
    print("РОБОТ ЗАПУЩЕН")
    print("========================================")
    print(f'Wake-фраза: "{WAKE_PHRASE}"')
    print("Состояние: ОЖИДАНИЕ")
    print("========================================")
    print()

    while not stop_event.is_set():
        try:
            audio = audio_queue.get(timeout=0.2)
        except queue.Empty:
            continue

        try:
            if not listening_for_command:
                pre_roll.append(audio)
                accepted = wake_recognizer.AcceptWaveform(audio)

                if accepted:
                    wake_text = json.loads(wake_recognizer.Result()).get("text", "")
                else:
                    wake_text = json.loads(wake_recognizer.PartialResult()).get("partial", "")

                if wake_text:
                    sys.stdout.write(f"\r[Ожидание] {wake_text}       ")
                    sys.stdout.flush()

                if contains_wake_phrase(wake_text):
                    print("\n\n========================================")
                    print("ПРОБУЖДЕНИЕ!")
                    print("========================================")
                    listening_for_command = True
                    command_audio_chunks = list(pre_roll)
                    command_recognizer.Reset()

                    for buffered_audio in pre_roll:
                        command_recognizer.AcceptWaveform(buffered_audio)

                    wake_recognizer.Reset()
                    pre_roll.clear()
                    print("Состояние: СЛУШАЮ КОМАНДУ\n")
                elif accepted:
                    wake_recognizer.Reset()
                continue

            # Режим распознавания команды после wake word
            if command_audio_chunks is not None:
                command_audio_chunks.append(audio)

            accepted = command_recognizer.AcceptWaveform(audio)

            if not accepted:
                partial = json.loads(command_recognizer.PartialResult()).get("partial", "")
                if partial:
                    sys.stdout.write(f"\r[Команда] {partial}       ")
                    sys.stdout.flush()
                continue

            text = json.loads(command_recognizer.Result()).get("text", "").strip()

            text = re.sub(rf"\b{re.escape(normalize(WAKE_PHRASE))}\b", " ", text, count=1, flags=re.IGNORECASE)
            text = " ".join(text.split()).strip(" ,.!?;:")

            if text:
                print("\n========================================")
                print("[КОМАНДА]")
                print(text)
                print("========================================")
                save_result(text, command_audio_chunks)

            command_audio_chunks = None
            listening_for_command = False
            pre_roll.clear()
            wake_recognizer.Reset()
            command_recognizer.Reset()

            print("\nСостояние: ОЖИДАНИЕ")
            print(f'Жду "{WAKE_PHRASE}"...\n')

        except Exception as exc:
            print(f"\n[ОШИБКА РАСПОЗНАВАНИЯ] {exc}", file=sys.stderr)
            listening_for_command = False
            command_audio_chunks = None
            pre_roll.clear()
            wake_recognizer.Reset()
            command_recognizer.Reset()
        finally:
            try:
                audio_queue.task_done()
            except ValueError:
                pass


# ============================================================
# ЗАПУСК
# ============================================================

def main():
    global audio_writer_thread, speech_thread, api_thread, api_server_thread

    if not MODEL_PATH.is_dir():
        raise FileNotFoundError(
            f"Модель не найдена: {MODEL_PATH}\n"
            "Папка vosk-model-ru-0.42 должна лежать рядом с main.py."
        )

    print()
    print("========================================")
    print("ЗАГРУЗКА VOSK RUSSIAN 0.42")
    print("========================================")
    print(f"Модель: {MODEL_PATH}")
    print(f"Логи:   {LOG_DIR}")
    print()

    model = Model(str(MODEL_PATH))
    print("Модель загружена!\n")

    api_thread = threading.Thread(target=api_sender, name="api-sender", daemon=True)
    api_thread.start()

    api_server_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="warning"),
        name="fastapi-server",
        daemon=True,
    )
    api_server_thread.start()
    time.sleep(0.5)

    audio_writer_thread = threading.Thread(target=audio_writer, name="audio-writer", daemon=True)
    audio_writer_thread.start()

    speech_thread = threading.Thread(
        target=speech_worker,
        args=(model,),
        name="speech-worker",
        daemon=True,
    )
    speech_thread.start()

    try:
        print("Запуск микрофона...")
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            dtype="int16",
            channels=CHANNELS,
            callback=microphone_callback,
        ):
            print("Микрофон запущен!")
            print(f'Скажите "{WAKE_PHRASE}", затем произнесите команду.')
            print(f"Веб-интерфейс: http://{API_HOST}:{API_PORT}/")
            print(f"Документация API: http://{API_HOST}:{API_PORT}/docs\n")

            while True:
                sd.sleep(500)

    except KeyboardInterrupt:
        print("\nОстановка программы...")

    finally:
        stop_event.set()

        # Дожидаемся записи оставшихся аудиоблоков
        try:
            audio_record_queue.put(None, timeout=2)
            if audio_writer_thread:
                audio_writer_thread.join(timeout=5)
        except queue.Full:
            print("[ЛОГИ] Не удалось завершить очередь записи вовремя.", file=sys.stderr)

        # Дожидаемся обработки отправленных команд
        try:
            send_queue.join()
        except Exception:
            pass

        try:
            audio_file.close()
        except Exception:
            pass

        try:
            transcript_file.close()
        except Exception:
            pass

        # Полная запись микрофона в MP3 м при отсутствии FFmpeg оставляем WAV
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path and WAV_PATH.exists():
            try:
                subprocess.run(
                    [
                        ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(WAV_PATH),
                        "-codec:a", "libmp3lame", "-q:a", "4",
                        str(MP3_PATH),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                WAV_PATH.unlink(missing_ok=True)
                print(f"[ЛОГИ] Полная запись микрофона: {MP3_PATH.resolve()}")
            except Exception as exc:
                print(f"[ЛОГИ] Не удалось создать MP3: {exc}", file=sys.stderr)
                print(f"[ЛОГИ] Исходный WAV: {WAV_PATH.resolve()}")
        else:
            print(f"[ЛОГИ] FFmpeg не найден; полная запись микрофона в WAV: {WAV_PATH.resolve()}")

        print(f"[ЛОГИ] Текстовый лог: {TRANSCRIPT_PATH.resolve()}")
        print(f"[ЛОГИ] Аудио команд: {COMMANDS_DIR.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\n========================================")
        print("ОШИБКА ЗАПУСКА")
        print("========================================")
        print(exc)
        print("========================================")
        sys.exit(1)
