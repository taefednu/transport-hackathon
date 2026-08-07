"""Прогрев кэша модели перед показом.

Зачем. Живой вызов YandexGPT занимает 2–2.5 секунды. На сцене это выглядит как
зависший продукт. Клиент кэширует ответы по хешу тела запроса, поэтому та же
фраза второй раз отдаётся мгновенно — кэш настоящий и работает так всегда,
прогрев лишь делает первый вызов заранее, а не при зрителях.

Важно: кэш живёт в памяти процесса сервера. Греть надо **тот самый** процесс,
с которого пойдёт показ, и не перезапускать его после прогрева.

Запуск: `.venv/bin/python scripts/warm_demo.py [порт]`
"""

import _bootstrap  # noqa: F401

import json
import sys
import time
import urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8023}"

# фразы показа: держать этот список тем же, что и в сценарии демонстрации,
# иначе прогрет будет не тот запрос
DEMO_PHRASES = (
    "продлить четырнадцатый до Янги махалля",
    "продлить четырнадцатый до Куйлюка и посмотреть, что будет в утренний пик",
    "поставить на восьмёрке интервал 6 минут",
)

# вопросы ассистента: каждый стоит двух вызовов модели (выбор инструмента и
# пересказ), поэтому холодный вопрос на сцене — это пять секунд ожидания или
# уход на шаблонный ответ по таймауту
DEMO_QUESTIONS = (
    "какие маршруты требуют внимания",
    "расскажи про маршрут 29",
    "что можно сделать с маршрутом 8",
)


def post(path: str, payload: dict) -> tuple[dict, float]:
    started = time.perf_counter()
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        body = json.loads(response.read())
    return body, (time.perf_counter() - started) * 1000


def main() -> None:
    status = json.loads(urllib.request.urlopen(BASE + "/api/llm").read())
    if not status["available"]:
        print(f"Модель недоступна ({status['reason']}) — греть нечего.")
        print("Оба эндпоинта ответят детерминированно, показ не сломается.")
        return

    print(f"Модель: {status['model']}, таймаут {status['timeout_sec']} с\n")
    for phrase in DEMO_PHRASES:
        cold, cold_ms = post("/api/nl/scenario", {"text": phrase})
        warm, warm_ms = post("/api/nl/scenario", {"text": phrase})
        print(f"«{phrase}»")
        print(f"  разбор: {cold_ms:6.0f} мс ({cold['source']}) → {warm_ms:6.0f} мс ({warm['source']})")

        if not cold["scenario"]:
            print(f"  сценарий не собран: {cold['understood']}")
            continue

        result, _ = post("/api/scenario", cold["scenario"])
        cold_text, cold_text_ms = post("/api/explain", {"result": result})
        warm_text, warm_text_ms = post("/api/explain", {"result": result})
        print(
            f"  абзац:  {cold_text_ms:6.0f} мс ({cold_text['source']}) → "
            f"{warm_text_ms:6.0f} мс ({warm_text['source']})"
        )

    print()
    for question in DEMO_QUESTIONS:
        cold, cold_ms = post("/api/assistant", {"text": question})
        warm, warm_ms = post("/api/assistant", {"text": question})
        print(f"«{question}»")
        print(
            f"  ассистент: {cold_ms:6.0f} мс ({cold['source']}) → "
            f"{warm_ms:6.0f} мс ({warm['source']}), "
            f"инструменты {[s['tool'] for s in warm['steps']]}"
        )

    cached = json.loads(urllib.request.urlopen(BASE + "/api/llm").read())["cached_answers"]
    print(f"\nВ кэше {cached} ответов. Сервер до показа не перезапускать.")


if __name__ == "__main__":
    main()
