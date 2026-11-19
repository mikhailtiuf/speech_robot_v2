"""Проверка ключевого слова."""
import state as s

def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    for char in ",.!?;:\"'()[]{}":
        text = text.replace(char, " ")
    return " ".join(text.split())


def contains_wake_phrase(text: str) -> bool:
    normalized = f" {normalize(text)} "
    phrase = f" {normalize(s.WAKE_PHRASE)} "
    return phrase in normalized

