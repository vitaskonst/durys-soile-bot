# Pinned by tag and digest, so every rebuild starts from the same Python and
# Debian patch level.
FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# mpg123 decodes the MP3 clips and opusenc re-encodes them as OGG/Opus voice
# notes, the only format Telegram draws a voice message's waveform for.
RUN apt-get update \
    && apt-get install -y --no-install-recommends mpg123 opus-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py ./

RUN useradd --create-home --uid 10001 bot
USER bot

# TOKEN and API_BASE_URL are supplied at run time (see README.md); nothing
# secret or deployment-specific is baked into the image.
CMD ["python", "bot.py"]
