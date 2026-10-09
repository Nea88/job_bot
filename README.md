# job-bot

Telegram-бот: раз в час собирает вакансии с hh.ru, Habr Career и Telegram-каналов по фильтрам
пользователей, присылает новые совпадения, в фоне извлекает требования локальной LLM (Ollama)
и собирает тестовое интервью по самым востребованным навыкам.

## Запуск локально

```bash
cp .env.example .env          # BOT_TOKEN, ADMIN_IDS, ...
ollama pull qwen2.5:7b-instruct
uv sync
uv run job-bot
```

Telegram-каналы (опционально): заполнить `TG_API_ID`/`TG_API_HASH` (https://my.telegram.org),
один раз выполнить `uv run python scripts/telethon_login.py`, затем админом: `/channels add <username>`.

## Docker

```bash
docker compose up -d --build
docker compose run --rm bot uv run python scripts/telethon_login.py   # если нужны каналы
```

## Команды

| Команда | |
|---|---|
| `/filters` | список, создание (мастер), удаление фильтров |
| `/interview` | тестовое интервью по фильтру (пошагово, с ответами) |
| `/stats` | топ навыков по вакансиям фильтра за 30 дней |
| `/daily` | ежедневное интервью в `DAILY_INTERVIEW_HOUR` |
| `/pause`, `/resume` | рассылка |
| `/channels`, `/run_now` | админ: каналы, внеочередной сбор |

Ключевые слова: целые слова, `*` в конце — по началу слова (`разработ*`).

## Как устроено

- `pipeline/collect.py` — часовой сбор: одинаковые фильтры объединяются в один запрос, дедуп по
  `source+id` и по «название+компания» между источниками, `matching.py` — единые правила фильтра.
- `pipeline/analyze.py` — очередь анализа через Ollama с JSON-схемой; навыки нормализуются по
  `skills_aliases.yaml`.
- `interview/builder.py` — агрегат навыков → вопросы от LLM.

## Тесты

```bash
uv run pytest
```
