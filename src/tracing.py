import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

TRACE_HEADER = "X-Trace-Id"
MAX_TRACE_ID_LENGTH = 128
LOG_FORMAT = "%(asctime)s %(levelname)-5s [trace_id=%(trace_id)s] %(name)s: %(message)s"

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    return str(uuid.uuid4())


def get_trace_id() -> str | None:
    return _trace_id.get()


@contextmanager
def trace_scope(trace_id: str | None = None) -> Iterator[str]:
    resolved = trace_id or new_trace_id()
    token = _trace_id.set(resolved)
    try:
        yield resolved
    finally:
        _trace_id.reset(token)


def trace_headers() -> dict[str, str]:
    trace_id = get_trace_id()
    if trace_id is None:
        return {}
    return {TRACE_HEADER: trace_id}


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id() or "-"
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(TraceIdFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


class TraceIdMiddleware:
    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        trace_id = self._incoming_trace_id(scope)

        async def send_with_trace_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-trace-id", trace_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        with trace_scope(trace_id):
            await self._app(scope, receive, send_with_trace_id)

    def _incoming_trace_id(self, scope: dict[str, Any]) -> str:
        for name, value in scope.get("headers") or []:
            if name.decode("latin-1").lower() == TRACE_HEADER.lower():
                candidate = value.decode("latin-1").strip()[:MAX_TRACE_ID_LENGTH]
                if candidate:
                    return candidate
                break
        return new_trace_id()
