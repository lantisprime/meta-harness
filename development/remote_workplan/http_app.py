"""Optional FastAPI surface for the Linear projection adapter.

This is an application factory only; it does not claim that a service is
deployed or production-ready.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .linear import LinearProjectionAdapter, WebhookVerificationError


def create_app(adapter: LinearProjectionAdapter) -> FastAPI:
    app = FastAPI(title="Remote Workplan Linear Projection", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": "projection"}

    @app.post("/webhooks/linear")
    async def linear_webhook(request: Request) -> JSONResponse:
        raw_body = await request.body()
        try:
            receipt = adapter.receive_webhook(raw_body=raw_body, headers=request.headers)
        except WebhookVerificationError as exc:
            status_code = 408 if exc.code == "stale_timestamp" else 401
            return JSONResponse({"accepted": False, "error": exc.code}, status_code=status_code)
        # A 200 acknowledges that the authenticated delivery was durably put in
        # the inbox. Processing and projection dispatch happen out of request.
        return JSONResponse(
            {
                "accepted": True,
                "delivery_id": receipt.delivery_id,
                "duplicate": receipt.duplicate,
                "reordered": receipt.reordered,
            }
        )

    return app
