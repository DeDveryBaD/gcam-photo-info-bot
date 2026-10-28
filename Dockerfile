FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends exiftool \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot

RUN useradd --create-home --uid 10001 botuser \
 && mkdir -p /tmp/gcam-bot \
 && chown -R botuser:botuser /app /tmp/gcam-bot
USER botuser

CMD ["python", "-m", "bot.main"]
