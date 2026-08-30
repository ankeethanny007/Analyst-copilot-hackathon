import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router


_LOCAL_DEVELOPMENT_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3004",
    "http://127.0.0.1:3004",
)


def _cors_origins() -> list[str]:
    """Return explicit browser origins without widening production CORS.

    The demo is commonly run on a temporary Next port when another local
    server already owns 3000.  Keep those loopback origins available during
    development while leaving a deployed environment entirely controlled by
    its explicit ``CORS_ORIGINS`` setting.
    """
    configured = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", ",".join(_LOCAL_DEVELOPMENT_ORIGINS)).split(",")
        if origin.strip()
    ]
    if os.getenv("APP_ENV", "development") == "development":
        configured.extend(_LOCAL_DEVELOPMENT_ORIGINS)
    return list(dict.fromkeys(configured))


app = FastAPI(title="Analyst Copilot API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(router, prefix="/v1")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
