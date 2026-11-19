"""Приём PCM-аудио с микрофона и распределение по очередям."""
import queue
import sys
import state as s

def microphone_callback(indata, frames, time_info, status):

    if status:
        print(f"[AUDIO] {status}", file=sys.stderr)

    audio = bytes(indata)

    try:
        s.audio_record_queue.put_nowait(audio)
    except queue.Full:
        if not s.audio_log_overflow_reported:
            print(
                "[AUDIO LOG] Очередь записи переполнена; часть аудио может потеряться.",
                file=sys.stderr,
            )
            s.audio_log_overflow_reported = True

    try:
        s.audio_queue.put_nowait(audio)
    except queue.Full:
        # Если распознавание отстаёт - выбрасываем старый блок
        try:
            s.audio_queue.get_nowait()
            s.audio_queue.task_done()
        except queue.Empty:
            pass
        try:
            s.audio_queue.put_nowait(audio)
        except queue.Full:
            pass

