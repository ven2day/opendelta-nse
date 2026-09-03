FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir "pandas>=3.0.5,<4" \
    && groupadd --gid 10001 collector \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin collector

COPY backend ./backend
COPY data ./data

USER 10001:10001

ENTRYPOINT ["python", "-m", "backend.collector"]
