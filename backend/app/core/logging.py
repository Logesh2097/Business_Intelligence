"""JSON logs, one line per event, and a request id on every request.

Never log a password, a token, a connection string or a customer's personal
detail (docs/design/architecture.md §8).
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from fastapi import Request, Response

from app.core.errors import internal_error_response
from app.core.request_context import request_id_var

_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            line["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                line[key] = value
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn's own access log would duplicate ours
    logging.getLogger("uvicorn.access").disabled = True


log = logging.getLogger("bi.request")


async def request_logging_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = uuid.uuid4().hex
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            # Unexpected: the cause goes to the log, the caller gets one generic sentence.
            log.exception(
                "unhandled error",
                extra={"method": request.method, "path": request.url.path},
            )
            response = internal_error_response()
        response.headers["X-Request-Id"] = request_id
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response
    finally:
        request_id_var.reset(token)
