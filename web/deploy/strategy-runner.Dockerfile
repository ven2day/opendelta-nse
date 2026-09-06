FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir "numpy>=2.3,<3" "pandas>=3.0.5,<4" \
    && groupadd --gid 10001 runner \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin runner

COPY backend ./backend

USER 10001:10001

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD ["python", "-c", "import os; assert os.path.exists('/run/opendelta-strategy/runner.sock')"]

CMD ["python", "-m", "backend.strategies.runner_v2", "--socket", "/run/opendelta-strategy/runner.sock"]
