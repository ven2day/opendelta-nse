FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir \
      "fastapi>=0.116,<1" \
      "numpy>=2.3,<3" \
      "pandas>=3.0.5,<4" \
      "pyarrow>=21,<22" \
      "uvicorn>=0.35,<1" \
      "websockets>=15,<16" \
    && groupadd --gid 10001 backtest \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin backtest

COPY main.py backtest_api.py backtest_history.py backtest_jobs.py application_settings.py recovery_backtest.py recovery_position_backtest.py recovery_dynamic_exit.py recovery_rsi_profit_exit.py atr_exit_optimizer.py rsi_exit_optimizer.py recovery_feature_analysis.py universe_selection.py live_signals.py market_data_refresh.py market_symbol_registry.py market_aligned_vwap_pullback_scalper.py nifty_oi_regime.py dhan_oi.py dhan_oi_history.py import_nifty_oi_history.py strategy_parameters.py strategy-parameters.json symbols.csv ./

EXPOSE 8000
USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["uvicorn", "backtest_api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
