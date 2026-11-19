"""Отправка распознанных команд через HTTP API."""
import queue
import sys
from datetime import datetime
import requests
import state as s
from storage import save_audio_segment

def api_sender():
    while not s.stop_event.is_set() or not s.send_queue.empty():
        try:
            item = s.send_queue.get(timeout=0.2)
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
                response = requests.post(s.API_URL, json=payload, timeout=2)
                print(f"[API ОТПРАВКА] {response.status_code} → {text}")
            except requests.RequestException as exc:
                print(f"[ОШИБКА API] {exc}")

            save_audio_segment(audio_chunks)

        except Exception as exc:
            print(f"[ОШИБКА ПОТОКА ОТПРАВКИ] {exc}", file=sys.stderr)
        finally:
            s.send_queue.task_done()

