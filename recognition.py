"""Распознавание Vosk 0.42 и переключение режимов ожидания/команды."""
import json
import queue
import re
import sys
from collections import deque
from vosk import KaldiRecognizer
import state as s
from activation import contains_wake_phrase, normalize
from storage import save_result

def speech_worker(model):

    wake_recognizer = KaldiRecognizer(model, s.SAMPLE_RATE)
    command_recognizer = KaldiRecognizer(model, s.SAMPLE_RATE)

    pre_roll = deque(maxlen=12)  # примерно 3 секунды при s.BLOCK_SIZE=4096

    try:
        wake_recognizer.SetEndpointerDelays(0.5, 0.3, 10.0)
        command_recognizer.SetEndpointerDelays(0.5, 0.3, 10.0)
    except (AttributeError, TypeError):
        pass

    print()
    print("========================================")
    print("РОБОТ ЗАПУЩЕН")
    print("========================================")
    print(f'Wake-фраза: "{s.WAKE_PHRASE}"')
    print("Состояние: ОЖИДАНИЕ")
    print("========================================")
    print()

    while not s.stop_event.is_set():
        try:
            audio = s.audio_queue.get(timeout=0.2)
        except queue.Empty:
            continue

        try:
            if not s.listening_for_command:
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
                    s.listening_for_command = True
                    s.command_audio_chunks = list(pre_roll)
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
            if s.command_audio_chunks is not None:
                s.command_audio_chunks.append(audio)

            accepted = command_recognizer.AcceptWaveform(audio)

            if not accepted:
                partial = json.loads(command_recognizer.PartialResult()).get("partial", "")
                if partial:
                    sys.stdout.write(f"\r[Команда] {partial}       ")
                    sys.stdout.flush()
                continue

            text = json.loads(command_recognizer.Result()).get("text", "").strip()

            text = re.sub(rf"\b{re.escape(normalize(s.WAKE_PHRASE))}\b", " ", text, count=1, flags=re.IGNORECASE)
            text = " ".join(text.split()).strip(" ,.!?;:")

            if text:
                print("\n========================================")
                print("[КОМАНДА]")
                print(text)
                print("========================================")
                save_result(text, s.command_audio_chunks)

            s.command_audio_chunks = None
            s.listening_for_command = False
            pre_roll.clear()
            wake_recognizer.Reset()
            command_recognizer.Reset()

            print("\nСостояние: ОЖИДАНИЕ")
            print(f'Жду "{s.WAKE_PHRASE}"...\n')

        except Exception as exc:
            print(f"\n[ОШИБКА РАСПОЗНАВАНИЯ] {exc}", file=sys.stderr)
            s.listening_for_command = False
            s.command_audio_chunks = None
            pre_roll.clear()
            wake_recognizer.Reset()
            command_recognizer.Reset()
        finally:
            try:
                s.audio_queue.task_done()
            except ValueError:
                pass

