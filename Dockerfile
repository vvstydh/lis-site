# Сайт регистрации «Долгожданный треш! Сумеречный лес»
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DB_PATH=/app/data/registrations.db

WORKDIR /app

# отдельный пользователь без прав root и без shell
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install -r requirements.txt

# код только для чтения, писать можно лишь в data/
COPY app.py ./
COPY templates ./templates
COPY static ./static
RUN mkdir -p /app/data /app/logs && chown app:app /app/data /app/logs && chmod 700 /app/data

USER app
VOLUME ["/app/data"]
ENV LOG_DIR=/app/logs
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

# один процесс + потоки: SQLite и счётчики лимитов живут в одном процессе
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8000", \
     "--timeout", "30", "--graceful-timeout", "10", \
     "--limit-request-line", "4094", "--limit-request-fields", "50", "--limit-request-field_size", "8190", \
     "--worker-tmp-dir", "/tmp", "app:app"]
