import os
import random
import threading
import time
import uuid
from collections import deque

import requests
import urllib3
from flask import Flask, jsonify, render_template


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# CONFIG
# ============================================================

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://api.giga.chat/v1/chat/completions"

GIGACHAT_MODEL = "GigaChat-2"
REQUEST_TIMEOUT = 30


# Получаем секреты из переменных окружения.
# В GitHub реальные ключи НЕ храним.

GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
GIGACHAT_SCOPE = os.getenv(
    "GIGACHAT_SCOPE",
    "GIGACHAT_API_PERS"
).strip()


# ============================================================
# SSL
# ============================================================

# В текущем прототипе используется verify=False,
# поэтому отключаем соответствующее предупреждение.
#
# В production в дальнейшем лучше подключить
# корректный CA certificate GigaChat.

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


# ============================================================
# FALLBACK-ПОЖЕЛАНИЯ
# ============================================================

FALLBACK_WISHES = [
    "Пусть сегодняшний день подарит тебе много приятных моментов и поводов улыбнуться.",
    "Желаю тебе вдохновения, хорошего настроения и лёгкости во всех сегодняшних делах.",
    "Пусть этот день принесёт тебе тепло, радость и приятные неожиданности.",
    "Желаю прекрасного дня, лёгких решений и множества хороших моментов.",
    "Пусть сегодня всё получается легко, а настроение остаётся прекрасным до самого вечера.",
    "Желаю тебе энергии для важных дел и времени насладиться приятными моментами.",
    "Пусть сегодняшний день будет наполнен уютом, вдохновением и улыбками.",
    "Желаю начать этот день с хорошего настроения и закончить его с улыбкой.",
    "Пусть сегодня тебя сопровождают удача, лёгкость и прекрасное настроение.",
    "Желаю тебе красивого дня, добрых встреч и приятных событий.",
    "Пусть сегодняшний день принесёт новые идеи и немного приятного волшебства.",
    "Желаю тебе сил для всего задуманного и множества поводов улыбнуться.",
    "Пусть сегодня найдётся много моментов, которые захочется запомнить.",
    "Желаю тебе тёплого настроения, вдохновения и прекрасного дня.",
    "Пусть сегодняшний день подарит тебе приятные совпадения и хорошие новости.",
    "Желаю лёгкого дня, интересных идей и ощущения, что всё складывается как надо.",
    "Пусть сегодняшний день будет добрым и наполненным приятными мелочами.",
    "Желаю тебе отличного настроения, уверенности и удачи во всех сегодняшних делах.",
    "Пусть сегодня будет больше поводов радоваться, улыбаться и наслаждаться моментом.",
    "Желаю тебе дня, наполненного хорошей энергией, вдохновением и приятными событиями."
]


recent_wishes = deque(maxlen=20)


# ============================================================
# TOKEN CACHE
# ============================================================

TOKEN_CACHE = {
    "token": None,
    "expires_at": 0
}

TOKEN_LOCK = threading.Lock()


# ============================================================
# GIGACHAT ACCESS TOKEN
# ============================================================

def get_access_token():
    """
    Возвращает действующий Access Token GigaChat.

    Если токена нет или срок его действия подходит к концу,
    автоматически получает новый.
    """

    if not GIGACHAT_AUTH_KEY:
        raise RuntimeError(
            "Не задана переменная окружения GIGACHAT_AUTH_KEY"
        )

    if not GIGACHAT_SCOPE:
        raise RuntimeError(
            "Не задана переменная окружения GIGACHAT_SCOPE"
        )

    with TOKEN_LOCK:

        # Используем существующий токен,
        # если он ещё действителен.

        if (
            TOKEN_CACHE["token"]
            and time.time() < TOKEN_CACHE["expires_at"]
        ):
            return TOKEN_CACHE["token"]

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",
        }

        data = {
            "scope": GIGACHAT_SCOPE
        }

        response = requests.post(
            OAUTH_URL,
            headers=headers,
            data=data,
            timeout=REQUEST_TIMEOUT,
            verify=False
        )

        response.raise_for_status()

        result = response.json()

        token = result.get("access_token")

        if not token:
            raise RuntimeError(
                "GigaChat не вернул access_token"
            )

        TOKEN_CACHE["token"] = token

        # Access Token действует около 30 минут.
        # Обновляем его немного заранее.

        TOKEN_CACHE["expires_at"] = (
            time.time() + 25 * 60
        )

        return token


# ============================================================
# SAFETY
# ============================================================

BLOCKED_WORDS = [
    "смерть",
    "умер",
    "погиб",
    "болезн",
    "страх",
    "тревог",
    "насили",
    "убий",
    "войн",
    "политик",
    "президент",
    "правительств",
    "выбор",
    "религи",
    "врач",
    "лекарств",
    "диагноз",
    "лечени",
    "инвестиц",
    "кредит",
    "криптовалют"
]


def is_safe_wish(text):
    """
    Простая дополнительная проверка
    сгенерированного пожелания.
    """

    if not text:
        return False

    text = text.strip()

    if len(text) < 10:
        return False

    if len(text) > 300:
        return False

    lower_text = text.lower()

    for word in BLOCKED_WORDS:
        if word in lower_text:
            return False

    return True


# ============================================================
# FALLBACK
# ============================================================

def get_fallback_wish():
    """
    Возвращает локальное пожелание,
    если GigaChat временно недоступен.
    """

    available = [
        wish
        for wish in FALLBACK_WISHES
        if wish not in recent_wishes
    ]

    if not available:
        available = FALLBACK_WISHES

    wish = random.choice(available)

    recent_wishes.append(wish)

    return wish


# ============================================================
# PROMPT
# ============================================================

WISH_PROMPT = """
Ты — генератор добрых пожеланий на день.

Придумай одно короткое, теплое, позитивное и
мотивирующее пожелание на день.

Требования:
- только один текст пожелания;
- только русский язык;
- без заголовка;
- без кавычек;
- без списков;
- без объяснений;
- без упоминания ИИ;
- длина 1–3 предложения;
- максимум 250 символов;
- стиль добрый, уютный и поддерживающий;
- пожелание должно подходить практически любому человеку;
- можно использовать образы дня, удачи, хорошего настроения,
  энергии, радости и вдохновения.

Строго запрещены:
- негатив;
- тревога;
- страх;
- болезни;
- смерть;
- потеря;
- насилие;
- политика;
- религия;
- оскорбления;
- медицинские советы;
- финансовые рекомендации.

Верни только само пожелание.
"""


# ============================================================
# GIGACHAT
# ============================================================

def generate_wish_from_gigachat():
    """
    Генерирует новое пожелание через GigaChat.
    """

    access_token = get_access_token()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        "model": GIGACHAT_MODEL,

        "messages": [
            {
                "role": "user",
                "content": WISH_PROMPT
            }
        ],

        "temperature": 0.9,
        "max_tokens": 150,
        "stream": False
    }

    response = requests.post(
        CHAT_URL,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT,
        verify=False
    )

    # Если access token неожиданно перестал работать,
    # сбрасываем кеш.

    if response.status_code == 401:
        TOKEN_CACHE["token"] = None
        TOKEN_CACHE["expires_at"] = 0

    response.raise_for_status()

    result = response.json()

    wish = (
        result
        .get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    # Иногда LLM может вернуть кавычки.
    # Убираем их.

    wish = wish.strip('"').strip("'").strip()

    if not is_safe_wish(wish):
        raise ValueError(
            "Пожелание не прошло safety-проверку"
        )

    # Не показываем точный повтор
    # недавно показанного пожелания.

    if wish in recent_wishes:
        raise ValueError(
            "Пожелание недавно уже показывалось"
        )

    recent_wishes.append(wish)

    return wish


# ============================================================
# ROUTES
# ============================================================

@app.route("/", methods=["GET"])
def index():
    """
    Главная страница.
    """

    return render_template("index.html")


@app.route("/api/wish", methods=["GET", "POST"])
def api_wish():
    """
    API для получения нового пожелания.
    """

    try:

        wish = generate_wish_from_gigachat()

        return jsonify({
            "ok": True,
            "wish": wish
        })

    except Exception as e:

        # Ошибка пишется только в серверный лог.
        # Пользователь её не увидит.

        print(
            "GigaChat error:",
            type(e).__name__,
            str(e),
            flush=True
        )

        wish = get_fallback_wish()

        return jsonify({
            "ok": False,
            "error": "GigaChat temporarily unavailable",
            "wish": wish
        })


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    """
    Простой endpoint для проверки,
    что приложение запущено.
    """

    return jsonify({
        "status": "ok"
    })


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv("PORT", "5000")
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )

