FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT', '38888') + '/healthz', timeout=3)" || exit 1

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-38888} --timeout 900 --graceful-timeout 120 --workers 1 --worker-class gthread --threads 4 --access-logfile - --error-logfile -"]
