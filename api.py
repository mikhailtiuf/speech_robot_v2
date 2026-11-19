"""Локальный FastAPI-сервер и веб-интерфейс."""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import state as s

app = FastAPI(title="Распознавание речи")

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


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/result")
def get_result():
    with s.data_lock:
        return {"text": s.latest_result}


@app.get("/history")
def get_history():
    with s.data_lock:
        return list(s.history)


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


