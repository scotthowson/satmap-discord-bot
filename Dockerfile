# The SatMap bot for Discord: a small Python program, nothing else in the image.
FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot.py satmap_data.py config.example.json VERSION ./
COPY art ./art

# An unprivileged user; the program writes only to the temporary folder (the board's message is found again in Discord).
RUN adduser -D -H -u 1000 satmap
USER satmap

# Healthy while it has been connected to Discord within the last two minutes.
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,sys,time; sys.exit(0 if time.time()-os.path.getmtime('/tmp/satmap-bot.alive')<120 else 1)"

ENTRYPOINT ["python", "bot.py"]
