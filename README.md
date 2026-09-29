# Дұрыс сөйле — Telegram bot

Browse the parasite and commonly mispronounced word lists and listen to their
pronunciation clips in Telegram.

## Configuration

Both settings come from the environment (or a `.env` file next to `bot.py`):

| Variable | |
|---|---|
| `TOKEN` | Telegram bot token from @BotFather |
| `API_BASE_URL` | Base URL of the Дұрыс сөйле API, e.g. `https://example.org/api/v1.0` |

The bot refuses to start if either is missing.

## Running locally

```bash
cp .env.example .env        # fill in TOKEN and API_BASE_URL
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Running with Docker

```bash
cp .env.example .env        # fill in TOKEN and API_BASE_URL
docker compose up -d --build
docker compose logs -f bot
```

The bot polls Telegram for updates, so it needs outbound HTTPS only; no
port is published.

The little state it keeps (the last voice message sent to each chat, so the
next one replaces it, and search filters too long for a button) is a SQLite
file in the `bot_data` volume, so it survives restarts and redeploys. Set
`STATE_DB` to put it elsewhere; without Docker it is `state.sqlite3` in the
working directory.

Run a single instance per token: Telegram delivers each update to one
poller, and a second one makes both fail with
`Conflict: terminated by other getUpdates request`.
