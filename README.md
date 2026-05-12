# MEXC Scanner

Python-скрипт + веб-интерфейс для мониторинга рынков биржи **MEXC**:
спотовых пар и бессрочных фьючерсов.

В интерфейсе две вкладки — **Спот** и **Фьючерсы**. По каждому символу выводится:

| Поле                  | Спот | Фьючерсы |
|-----------------------|------|----------|
| Последняя цена        | ✓    | ✓        |
| Bid / Ask             | ✓    | ✓        |
| 24ч изменение, %      | ✓    | ✓        |
| 24ч объём             | ✓    | ✓        |
| Ставка фандинга       | —    | ✓        |
| Статус депозита       | ✓¹   | ✓¹       |
| Статус вывода         | ✓¹   | ✓¹       |
| Адрес контракта       | ✓    | ✓¹       |

¹ требует API-ключи MEXC (read-only). Без ключей эти поля будут `N/A`,
а для адреса контракта на споте используется `contractAddress` из публичного
эндпоинта `/api/v3/exchangeInfo` (он есть не для всех монет).

## Используемые эндпоинты MEXC

Публичные:
- `GET https://api.mexc.com/api/v3/exchangeInfo` — список спот-пар, полные имена, контракт-адрес.
- `GET https://api.mexc.com/api/v3/ticker/24hr` — цена/bid/ask/объём по всем спот-парам.
- `GET https://contract.mexc.com/api/v1/contract/detail` — список бессрочных контрактов.
- `GET https://contract.mexc.com/api/v1/contract/ticker` — цена/bid/ask/объём контрактов.
- `GET https://contract.mexc.com/api/v1/contract/funding_rate` — фандинг по всем контрактам.

Приватные (нужны при необходимости статусов депозита/вывода и адресов по сетям):
- `GET https://api.mexc.com/api/v3/capital/config/getall` — справочник монет
  (подписан HMAC-SHA256 ключом).

## Запуск

Требуется Python 3.10+.

```bash
git clone https://github.com/evgetos/scanner.git
cd scanner
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env          # опционально, для API-ключей
python -m app.main
```

После запуска откройте `http://localhost:8000`.

### Опциональные переменные окружения

Файл `.env.example` содержит шаблон. Поддерживается:

- `MEXC_API_KEY`, `MEXC_API_SECRET` — read-only ключи MEXC (необязательны).
- `HOST` — адрес сервера (по умолчанию `0.0.0.0`).
- `PORT` — порт (по умолчанию `8000`).
- `CACHE_TTL` — TTL внутреннего кэша запросов к MEXC в секундах (по умолчанию `30`).

### Альтернативный запуск через uvicorn

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API сервера

- `GET /api/spot` — JSON со списком спот-пар.
- `GET /api/futures` — JSON со списком бессрочных контрактов.
- `POST /api/refresh` — сбросить серверный кэш.
- `GET /api/health` — состояние сервиса.

## Архитектура

```
app/
├── __init__.py
├── main.py            # FastAPI-приложение, маршруты, lifecycle
├── mexc.py            # HTTP-клиент MEXC (spot + futures, public + signed)
├── aggregator.py      # сборка строк таблицы из ответов MEXC
├── cache.py           # асинхронный TTL-кэш
└── static/
    ├── index.html     # SPA с двумя вкладками
    ├── styles.css
    └── app.js
```

Данные с биржи получаются параллельно (`asyncio.gather`) и кэшируются
на стороне сервера на `CACHE_TTL` секунд (30 по умолчанию), чтобы не упереться
в rate-limits MEXC при перезагрузке страницы.
