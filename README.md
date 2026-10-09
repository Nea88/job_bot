# job-bot

Telegram-бот: раз в час собирает вакансии с hh.ru, Habr Career и Telegram-каналов по фильтрам
пользователей, присылает новые совпадения, в фоне извлекает требования локальной LLM (Ollama)
и собирает тестовое интервью по самым востребованным навыкам.

## Home Assistant (Raspberry Pi)

[![Добавить репозиторий в Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FNea88%2Fjob_bot)

1. Настройки → Дополнения → Магазин дополнений → ⋮ → Репозитории → `https://github.com/Nea88/job_bot`.
2. Установи и запусти **Ollama**.
3. Установи **Job Bot**, укажи `bot_token` и `admin_ids`, запусти. Ollama и модель подхватятся сами.

Подробности: [job_bot/DOCS.md](job_bot/DOCS.md). Новая версия: подними `version` в
`job_bot/config.yaml` и запушь в `main`, тогда CI соберёт образ `ghcr.io/nea88/job_bot`, а HA покажет обновление.

## Запуск локально

```bash
cp .env.example .env          # BOT_TOKEN, ADMIN_IDS, ...
ollama pull qwen2.5:7b-instruct
uv sync
uv run job-bot
```

Telegram-каналы (опционально): заполнить `TG_API_ID`/`TG_API_HASH` (https://my.telegram.org),
один раз выполнить `uv run python scripts/telethon_login.py` (или с `--string` для аддона HA), затем админом: `/channels add <username>`.

## Docker

```bash
docker compose up -d --build
docker compose run --rm bot python scripts/telethon_login.py   # если нужны каналы
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
