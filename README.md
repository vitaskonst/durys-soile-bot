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
docker build -t durys-soile-bot .
docker run -d --restart unless-stopped --env-file .env durys-soile-bot
```
