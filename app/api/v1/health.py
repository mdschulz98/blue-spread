from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import SessionDep

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe (no database access)")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/readyz",
    summary="Readiness probe (runs a trivial database query)",
    responses={503: {"description": "Database unavailable"}},
)
async def readyz(session: SessionDep) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unavailable", "database": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok"})
