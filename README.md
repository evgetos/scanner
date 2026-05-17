# Crypto Arbitrage Scanner — Web UI

Веб-версия многобиржевого арбитражного сканера на FastAPI.
Сканер опрашивает 13 криптобирж, ищет спреды по bid/ask на USDT-парах,
сопоставляет статусы депозитов/выводов и общие сети между биржами,
а затем анализирует стаканы для пар с возможным трансфером.

Поддерживаемые биржи:
Binance, Bybit, OKX, Bitget, BingX, MEXC, KuCoin, Huobi, Gate.io,
Blofin, Hyperliquid, XT, Asterdex.

## Возможности

- Фоновый сканер, который автоматически запускается раз в `SCANNER_REFRESH_INTERVAL`
  секунд (по умолчанию 60).
- REST-эндпоинт `POST /api/scan` для ручного запуска с пользовательскими параметрами
  (прокси, минимальный объём, диапазон спреда).
- `GET /api/state` отдаёт последний результат скана в JSON.
- Дашборд `GET /` — сводка по биржам, таблица арбитражных возможностей
  с сортировкой и фильтрацией, анализ стаканов, статусы D/W и общие сети.

## Запуск локально

Нужен Python ≥ 3.10.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # либо: pip install -e .

python run.py                        # либо: uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Откройте <http://localhost:8000>.

## Запуск из PyCharm

1. **Открыть проект**: *File → Open* → выбрать корневую папку проекта.
2. **Настроить интерпретатор**: *Settings → Project: scanner → Python Interpreter →
   ⚙ → Add → Virtualenv Environment → New environment* (Python ≥ 3.10), нажать OK.
3. **Установить зависимости**: открыть встроенный терминал PyCharm (`Alt+F12`) и выполнить
   `pip install -r requirements.txt`.
4. **Запустить**: в верхней панели выбрать конфигурацию **Web Scanner** (она уже
   в репозитории, лежит в `.idea/runConfigurations/Web_Scanner.xml`) и нажать ▶.
   Альтернатива — открыть `run.py` и нажать *Run 'run'* (Ctrl+Shift+F10) или
   правым кликом → *Run 'run'*.

Логи uvicorn появятся в окне *Run*. Открыть <http://127.0.0.1:8000>.
Горячая перезагрузка включена, изменения в `app/` подхватываются автоматически.

Если нужны API-ключи или прокси — добавьте их в *Run Configuration → Environment
variables* (ключи `MEXC_API_KEY`, `BINANCE_API_KEY`, `SCANNER_PROXY` и т.д.,
полный список ниже).

## Переменные окружения

| Переменная | Назначение | По умолчанию |
| --- | --- | --- |
| `SCANNER_PROXY` | Прокси (`http://`, `https://`, `socks5://…`) | пусто |
| `SCANNER_MIN_VOLUME` | Минимальный 24ч объём, $ | `200000` |
| `SCANNER_MIN_SPREAD` | Минимальный спред, % | `0.5` |
| `SCANNER_MAX_SPREAD` | Максимальный спред, % | `20` |
| `SCANNER_OB_LIMIT` | Глубина стакана | `50` |
| `SCANNER_REFRESH_INTERVAL` | Интервал авто-рефреша, с | `60` |
| `SCANNER_AUTO_START` | Запускать ли фоновый цикл сразу | `1` |
| `SCANNER_ARBITRAGE_LIMIT` | Сколько строк показывать | `50` |
| `MEXC_API_KEY` / `MEXC_SECRET_KEY` | Для D/W статусов MEXC | — |
| `BINANCE_API_KEY` / `BINANCE_SECRET_KEY` | Для D/W статусов Binance | — |
| `BYBIT_API_KEY` / `BYBIT_SECRET_KEY` | Для D/W статусов Bybit | — |

Без API-ключей сканер всё равно работает: D/W статусы для соответствующих бирж
будут показаны как `?`, а проверка общих сетей ограничится биржами с публичными
эндпоинтами (Bitget, KuCoin, Huobi, Gate.io, XT).

### .env-файл

`run.py` автоматически подхватывает `.env` рядом с собой (через `python-dotenv`)
— в репозитории лежит шаблон `.env.example`. Скопируйте его:

```bash
cp .env.example .env
# и впишите ключи
```

`.env` уже в `.gitignore`, коммиты безопасны. При прямом вызове `uvicorn app.main:app`
файл НЕ подгружается — используйте `python run.py` либо выполните
`set -a && source .env && set +a` перед запуском.

## Структура

```
app/
  main.py          # FastAPI-приложение, фоновый сканер
  scanner_core.py  # Логика сканера: тикеры, D/W, общие сети, стаканы
  config.py        # Загрузка настроек из env
  static/
    index.html
    styles.css
    app.js
```

Оригинальная консольная версия скрипта (`scanner.py`) была преобразована в
библиотеку без потери логики — все парсеры тикеров, маппинг сетей, расчёт
спреда и анализ стакана остались идентичными.
