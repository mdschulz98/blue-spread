"""Pure-ASGI middleware: per-request ID (``X-Request-ID``) and a structured access log."""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_body
from app.core.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("app.access")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope["headers"]:
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        # Accept a caller-provided ID only if it is reasonably shaped (it ends up in logs).
        request_id = (
            incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        )
        token = request_id_var.set(request_id)
        status_code = 500
        response_started = False
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            # Render unhandled errors here (inside the request context) so they are logged with
            # the request ID and the response carries the header, in the standard error shape.
            logger.exception("unhandled error")
            if response_started:
                raise
            response = JSONResponse(
                error_body("internal_error", "Internal server error"), status_code=500
            )
            await response(scope, receive, send_with_request_id)
        finally:
            client = scope.get("client")
            access_logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "client": client[0] if client else None,
                },
            )
            request_id_var.reset(token)
