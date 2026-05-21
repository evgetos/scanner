# Fair vs Last Price Scanner

Парсер на Python / FastAPI, который периодически сравнивает **fair price** (mark price) и **last price** на USDT-маржинальных
бессрочных фьючерсах на шести биржах:

- **MEXC** — `/api/v1/contract/ticker`
- **Gate.io** — `/api/v4/futures/usdt/tickers`
- **Bybit** — `/v5/market/tickers?category=linear`
- **OKX** — `/api/v5/market/tickers?instType=SWAP` + `/api/v5/public/mark-price`
- **Bitget** — `/api/v2/mix/market/tickers?productType=USDT-FUTURES`
- **KuCoin** — `/api/v1/contracts/active`

При расхождении (`|last - fair| / fair * 100% ≥ min_spread_pct`) тикер попадает в таблицу аномалий в веб-интерфейсе.

## Что умеет

- Фоновый асинхронный сканер (`asyncio.gather` по всем биржам, выполняется каждые `scan_interval_sec` секунд).
- Веб-интерфейс с настройками:
  - включение/отключение каждой биржи,
  - минимальный объём торгов за 24 ч (USDT),
  - минимальный спред в процентах,
  - интервал сканирования,
  - **HTTP / HTTPS / SOCKS5 прокси** для всех запросов к биржам.
- Сохранение настроек в `data/config.json` между перезапусками.
- Сводка по биржам (зелёный/жёлтый/красный индикатор, количество тикеров, последняя ошибка).
- Таблица аномалий, отсортированная по абсолютной величине спреда.

## Запуск

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Открыть в браузере: <http://localhost:8000/>

### Через Docker

```bash
docker build -t scanner .
docker run -p 8000:8000 -v $(pwd)/data:/app/data scanner
```

## Прокси

В поле «Прокси-URL» можно указать:

- `http://user:pass@proxy.example.com:8080`
- `https://proxy.example.com:8080`
- `socks5://user:pass@proxy.example.com:1080`

Прокси применяется ко всем биржам (некоторые из них, например Bybit, блокируют запросы по гео-IP — без прокси они вернут
ошибку, которая будет видна на карточке биржи в UI).

## API

| Метод | Путь | Описание |
| ----- | ---- | -------- |
| GET   | `/api/exchanges`      | Список поддерживаемых бирж. |
| GET   | `/api/settings`       | Текущие настройки. |
| POST  | `/api/settings`       | Сохранить настройки (JSON `ScannerSettings`). |
| GET   | `/api/scan`           | Результат последнего сканирования. |
| POST  | `/api/scan/run`       | Запустить сканирование немедленно. |

Пример обновления настроек:

```bash
curl -X POST http://localhost:8000/api/settings \
  -H 'content-type: application/json' \
  -d '{
    "exchanges": {
      "mexc":   {"enabled": true},
      "gate":   {"enabled": true},
      "bybit":  {"enabled": true},
      "okx":    {"enabled": true},
      "bitget": {"enabled": true},
      "kucoin": {"enabled": true}
    },
    "min_volume_usdt": 1000000,
    "min_spread_pct": 0.5,
    "scan_interval_sec": 30,
    "proxy_url": "http://user:pass@proxy.example.com:8080"
  }'
```

## Структура

```
app/
├── main.py            # FastAPI, lifespan, маршруты
├── scanner.py         # Фоновый цикл, агрегация аномалий
├── settings.py        # Хранилище настроек (JSON-файл)
├── models.py          # Pydantic-модели
├── exchanges/         # Клиенты бирж (общий интерфейс fetch_tickers)
│   ├── base.py
│   ├── mexc.py
│   ├── gate.py
│   ├── bybit.py
│   ├── okx.py
│   ├── bitget.py
│   └── kucoin.py
└── static/            # Frontend (HTML/CSS/vanilla JS)
    ├── index.html
    ├── styles.css
    └── app.js
```

## Замечания

- На каждой бирже `fair price` имеет своё имя поля (`fairPrice`, `mark_price`, `markPrice`, `markPx`), это уже учтено
  в соответствующих клиентах.
- Символы нормализуются к виду `BASE/USDT` (XBT KuCoin → BTC).
- Объём 24 ч приводится к USDT для всех бирж (для OKX SWAP — `volCcy24h * last`, для остальных — родное поле в quote-валюте).
- Если биржа недоступна (например, без прокси), её ошибка отображается на карточке и не ломает остальное сканирование.
