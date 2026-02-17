from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="AntiHub Plugin Env Exporter", version="0.1.0")


def _getenv(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


class DbEnvResponse(BaseModel):
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    DB_USER: str
    DB_PASSWORD: str


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/migration/db-env", response_model=DbEnvResponse)
async def get_db_env(
    x_migration_token: Optional[str] = Header(default=None, alias="X-Migration-Token"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> DbEnvResponse:
    expected_token = _getenv("PLUGIN_ENV_EXPORT_TOKEN") or _getenv("PLUGIN_ADMIN_API_KEY") or _getenv("ADMIN_API_KEY")
    if expected_token:
        provided = None
        if x_migration_token:
            provided = x_migration_token.strip()
        if not provided and authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        if provided != expected_token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    # 兼容旧部署：支持 DB_* 与 PLUGIN_DB_* 两套命名
    host = _getenv("DB_HOST") or _getenv("PLUGIN_DB_HOST") or "postgres"
    port_raw = _getenv("DB_PORT") or _getenv("PLUGIN_DB_PORT") or "5432"
    name = _getenv("DB_NAME") or _getenv("PLUGIN_DB_NAME") or "antigravity"
    user = _getenv("DB_USER") or _getenv("PLUGIN_DB_USER") or "antigravity"
    password = _getenv("DB_PASSWORD") or _getenv("PLUGIN_DB_PASSWORD")

    missing = [k for k, v in [("DB_PASSWORD", password)] if not v]
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing env: {', '.join(missing)}")

    try:
        port = int(port_raw)
    except Exception:
        raise HTTPException(status_code=500, detail=f"Invalid DB_PORT: {port_raw!r}") from None

    return DbEnvResponse(
        DB_HOST=host,  # type: ignore[arg-type]
        DB_PORT=port,
        DB_NAME=name,  # type: ignore[arg-type]
        DB_USER=user,  # type: ignore[arg-type]
        DB_PASSWORD=password,  # type: ignore[arg-type]
    )
