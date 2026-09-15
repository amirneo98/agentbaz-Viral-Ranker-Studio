FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg      -> video pipeline (NVENC support via dlopen of the NVIDIA driver libs)
# fonts       -> LiberationSans-Bold used by the drawtext overlays
# curl        -> container healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-liberation \
        fontconfig \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x entrypoint.sh \
    && SECRET_KEY=build-time-key python manage.py collectstatic --noinput --settings=config.settings.prod

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
