"""Structured logging and request tracing for OpenDelta.

Wraps ``structlog`` with the stdlib logging backend so every existing
``logging.getLogger(...)`` call in the codebase participates in the same
pipeline. Events render as JSON lines on stdout in every environment, so one
machine-readable stream ships per container. Each HTTP request carries a
``request_id`` — taken from the incoming ``x-request-id`` header when present,
generated otherwise — that is bound into the logging context and echoed back
on the response, so multi-worker deployments can trace a single request end
to end.

Calling :func:`configure_logging` is idempotent and safe to repeat from the
FastAPI lifespan and from CLI entry points (``python -m backend.collector``).
Running under pytest never reconfigures handlers, so the test runner keeps its
own log capture.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import uuid
from typing import Any

import structlog

SERVICE_NAME = os.environ.get("OPENDELTA_SERVICE_NAME", "opendelta")
_REQUEST_ID_LENGTH = 12
_REQUEST_ID_HEADER = b"x-request-id"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _environment_is_testing() -> bool:
    """True when running under pytest (the runner owns the log handlers)."""
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _log_level() -> int:
    configured = os.environ.get("OPENDELTA_LOG_LEVEL", "INFO").upper()
    return getattr(logging, configured, logging.INFO)


_shared_processors: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.StackInfoRenderer(),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def configure_logging() -> None:
    """Install the structlog pipeline (once) and point stdlib at it."""
    if structlog.is_configured() or _environment_is_testing():
        return
    level = _log_level()

    structlog.configure(
        processors=[
            *_shared_processors,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Quiet the most verbose dependency loggers unless explicitly raised.
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        dependency = logging.getLogger(noisy)
        if dependency.level == logging.NOTSET:
            dependency.setLevel(logging.WARNING)


def get_logger(name: str = "opendelta") -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger for ``name`` (stdlib ``opendelta.*``)."""
    return structlog.get_logger(name)


async def log_lifespan_event(logger: structlog.stdlib.BoundLogger, event: str, **details: Any) -> None:
    """Emit a startup/shutdown event with the given context."""
    logger.info(event, **details)


class RequestContextMiddleware:
    """ASGI middleware that binds a ``request_id`` and request lifecycle events.

    The id is taken from the incoming ``x-request-id`` header when it is a
    safe short token, generated otherwise, and echoed back on the response.
    Because ``bind_contextvars`` uses Python ``contextvars``, the
    ``request_id`` automatically flows into every log call made by tasks
    spawned for this request, including ``asyncio.to_thread`` work.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = next(
            (value.decode("latin-1") for name, value in scope.get("headers", []) if name == _REQUEST_ID_HEADER),
            "",
        )
        request_id = supplied if _REQUEST_ID_PATTERN.match(supplied) else uuid.uuid4().hex[:_REQUEST_ID_LENGTH]
        request_logger = get_logger("opendelta.request")
        response_status: dict[str, Any] = {}
        started = time.perf_counter()

        async def response_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                response_status["status"] = message.get("status")
                headers = list(message.get("headers", []))
                if not any(name == _REQUEST_ID_HEADER for name, _ in headers):
                    headers.append((_REQUEST_ID_HEADER, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        with structlog.contextvars.bound_contextvars(
            request_id=request_id,
            method=scope.get("method", "GET"),
            path=scope.get("path", "/"),
        ):
            request_logger.info("request_start")
            try:
                await self.app(scope, receive, response_wrapper)
            finally:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                request_logger.info(
                    "request_finish",
                    status=response_status.get("status"),
                    duration_ms=duration_ms,
                    service=SERVICE_NAME,
                )


def install_observability(app: Any) -> None:
    """Wire request tracing into a FastAPI/Starlette application."""
    app.add_middleware(RequestContextMiddleware)
