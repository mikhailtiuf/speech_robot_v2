"""Общие настройки и состояние программы."""
import queue
import threading
import wave
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "vosk-model-ru-0.42"
SAMPLE_RATE = 16000
BLOCK_SIZE = 4096
CHANNELS = 1
WAKE_PHRASE = "городовой"
API_HOST = "127.0.0.1"
API_PORT = 8000
API_URL = f"http://{API_HOST}:{API_PORT}/receive"
AUDIO_QUEUE_SIZE = 100
AUDIO_RECORD_QUEUE_SIZE = 500
HISTORY_LIMIT = 500

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

latest_result = ""
history = []
data_lock = threading.Lock()
send_queue = queue.Queue()
audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_SIZE)
audio_record_queue = queue.Queue(maxsize=AUDIO_RECORD_QUEUE_SIZE)
audio_log_overflow_reported = False
listening_for_command = False
command_audio_chunks = None
stop_event = threading.Event()
