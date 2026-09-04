FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir \
      "fastapi>=0.116,<1" \
      "numpy>=2.3,<3" \
      "pandas>=3.0.5,<4" \
      "pyarrow>=21,<22" \
      "psycopg[binary,pool]>=3.2,<4" \
      "structlog>=25.5,<26" \
      "uvicorn>=0.35,<1" \
      "websockets>=15,<16" \
    && groupadd --gid 10001 backtest \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin backtest

COPY backend ./backend
COPY data ./data

EXPOSE 8000
USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
