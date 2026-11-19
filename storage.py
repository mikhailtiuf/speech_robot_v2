"""Сохранение текста, аудиофрагментов и полного аудиопотока."""
import shutil
import subprocess
import sys
import wave
from datetime import datetime
import state as s

def save_result(text: str, audio_chunks=None):

    current_time = datetime.now().strftime("%H:%M:%S")
    log_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with s.data_lock:
        s.latest_result = text
        s.history.append({"text": text, "time": current_time})
        if len(s.history) > s.HISTORY_LIMIT:
            del s.history[:-s.HISTORY_LIMIT]

    s.transcript_file.write(f"[{log_time}] {text}\n")
    s.transcript_file.flush()

    # Один элемент-словарь
    s.send_queue.put({
        "text": text,
        "audio_chunks": list(audio_chunks) if audio_chunks else [],
    })


def save_audio_segment(audio_chunks):
    if not audio_chunks:
        return

    timestamp = datetime.now().strftime("%H%M%S_%f")
    wav_path = s.COMMANDS_DIR / f"{timestamp}.wav"
    mp3_path = s.COMMANDS_DIR / f"{timestamp}.mp3"

    try:
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(s.CHANNELS)
            wav_file.setsampwidth(2)
            wav_file.setframerate(s.SAMPLE_RATE)
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


def audio_writer():
    while True:
        audio = s.audio_record_queue.get()
        try:
            if audio is None:
                return
            s.audio_file.writeframesraw(audio)
        except Exception as exc:
            print(f"[ОШИБКА ЗАПИСИ МИКРОФОНА] {exc}", file=sys.stderr)
        finally:
            s.audio_record_queue.task_done()


