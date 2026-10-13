FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# gthread workers: each chat WebSocket holds one thread, so keep plenty available.
CMD ["gunicorn", "--worker-class", "gthread", "--workers", "2", "--threads", "50", \
     "--timeout", "120", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "app:app"]
