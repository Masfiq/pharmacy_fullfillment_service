"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from app.api import inventory, orders, queue, stations, system
from app.api.errors import register_error_handlers

DASHBOARD = Path(__file__).resolve().parent / "static" / "dashboard.html"

DESCRIPTION = """
Backend for a simulated high volume (central fill) pharmacy.

**Learning project. Not for clinical use.** Drug data comes from the public
FDA NDC Directory; all patient identifiers are synthetic.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="Pharmacy Fulfillment Service",
        version="0.1.0",
        description=DESCRIPTION,
    )
    register_error_handlers(app)
    for module in (orders, queue, stations, inventory, system):
        app.include_router(module.router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(DASHBOARD)

    return app


app = create_app()
