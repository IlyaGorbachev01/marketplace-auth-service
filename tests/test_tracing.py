import logging
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.tracing import (
    LOG_FORMAT,
    TRACE_HEADER,
    TraceIdFilter,
    TraceIdMiddleware,
    get_trace_id,
    trace_headers,
    trace_scope,
)


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/probe")
    async def probe() -> dict[str, str | None]:
        return {"trace_id": get_trace_id()}

    return app


async def request(headers: dict[str, str] | None = None):
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/probe", headers=headers)


async def test_middleware_accepts_incoming_trace_id() -> None:
    response = await request({TRACE_HEADER: "demo-1"})

    assert response.status_code == 200
    assert response.headers["x-trace-id"] == "demo-1"
    assert response.json() == {"trace_id": "demo-1"}


async def test_middleware_generates_uuid4_when_header_missing() -> None:
    response = await request()

    trace_id = response.headers["x-trace-id"]
    assert uuid.UUID(trace_id).version == 4
    assert response.json() == {"trace_id": trace_id}


async def test_middleware_generates_uuid4_for_empty_header() -> None:
    response = await request({TRACE_HEADER: "   "})

    trace_id = response.headers["x-trace-id"]
    assert trace_id.strip()
    assert uuid.UUID(trace_id).version == 4


async def test_context_is_reset_after_request() -> None:
    assert get_trace_id() is None

    await request({TRACE_HEADER: "demo-2"})

    assert get_trace_id() is None
    assert trace_headers() == {}


def test_trace_scope_sets_and_resets_context() -> None:
    with trace_scope("trace-1") as trace_id:
        assert trace_id == "trace-1"
        assert get_trace_id() == "trace-1"
        assert trace_headers() == {TRACE_HEADER: "trace-1"}

    assert get_trace_id() is None


def test_trace_scope_generates_id_when_missing() -> None:
    with trace_scope() as trace_id:
        assert uuid.UUID(trace_id).version == 4
        assert get_trace_id() == trace_id


def test_filter_adds_trace_id_to_record() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    assert TraceIdFilter().filter(record) is True
    assert record.trace_id == "-"

    formatter = logging.Formatter(LOG_FORMAT)
    assert "[trace_id=-]" in formatter.format(record)

    with trace_scope("trace-42"):
        TraceIdFilter().filter(record)
        assert record.trace_id == "trace-42"
        assert "[trace_id=trace-42]" in formatter.format(record)
