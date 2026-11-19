"""Точка входа: запуск модели, API, микрофона и рабочих потоков."""
import shutil
import subprocess
import sys
import threading
import time
import queue
import sounddevice as sd
import uvicorn
from vosk import Model
import state as s
from api import app
from audio import microphone_callback
from recognition import speech_worker
from sender import api_sender
from storage import audio_writer

def main():

    if not s.MODEL_PATH.is_dir():
        raise FileNotFoundError(
            f"Модель не найдена: {s.MODEL_PATH}\n"
            "Папка vosk-model-ru-0.42 должна лежать рядом с main.py."
        )

    print()
    print("========================================")
    print("ЗАГРУЗКА VOSK RUSSIAN 0.42")
    print("========================================")
    print(f"Модель: {s.MODEL_PATH}")
    print(f"Логи:   {s.LOG_DIR}")
    print()

    model = Model(str(s.MODEL_PATH))
    print("Модель загружена!\n")

    api_thread = threading.Thread(target=api_sender, name="api-sender", daemon=True)
    api_thread.start()

    api_server_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host=s.API_HOST, port=s.API_PORT, log_level="warning"),
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
            samplerate=s.SAMPLE_RATE,
            blocksize=s.BLOCK_SIZE,
            dtype="int16",
            channels=s.CHANNELS,
            callback=microphone_callback,
        ):
            print("Микрофон запущен!")
            print(f'Скажите "{s.WAKE_PHRASE}", затем произнесите команду.')
            print(f"Веб-интерфейс: http://{s.API_HOST}:{s.API_PORT}/")
            print(f"Документация API: http://{s.API_HOST}:{s.API_PORT}/docs\n")

            while True:
                sd.sleep(500)

    except KeyboardInterrupt:
        print("\nОстановка программы...")

    finally:
        s.stop_event.set()

        # Дожидаемся записи оставшихся аудиоблоков
        try:
            s.audio_record_queue.put(None, timeout=2)
            if audio_writer_thread:
                audio_writer_thread.join(timeout=5)
        except queue.Full:
            print("[ЛОГИ] Не удалось завершить очередь записи вовремя.", file=sys.stderr)

        # Дожидаемся обработки отправленных команд
        try:
            s.send_queue.join()
        except Exception:
            pass

        try:
            s.audio_file.close()
        except Exception:
            pass

        try:
            s.transcript_file.close()
        except Exception:
            pass

        # Полная запись микрофона в MP3 м при отсутствии FFmpeg оставляем WAV
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path and s.WAV_PATH.exists():
            try:
                subprocess.run(
                    [
                        ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(s.WAV_PATH),
                        "-codec:a", "libmp3lame", "-q:a", "4",
                        str(s.MP3_PATH),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                s.WAV_PATH.unlink(missing_ok=True)
                print(f"[ЛОГИ] Полная запись микрофона: {s.MP3_PATH.resolve()}")
            except Exception as exc:
                print(f"[ЛОГИ] Не удалось создать MP3: {exc}", file=sys.stderr)
                print(f"[ЛОГИ] Исходный WAV: {s.WAV_PATH.resolve()}")
        else:
            print(f"[ЛОГИ] FFmpeg не найден; полная запись микрофона в WAV: {s.WAV_PATH.resolve()}")

        print(f"[ЛОГИ] Текстовый лог: {s.TRANSCRIPT_PATH.resolve()}")
        print(f"[ЛОГИ] Аудио команд: {s.COMMANDS_DIR.resolve()}")


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
