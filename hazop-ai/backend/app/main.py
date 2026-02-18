from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.database.cosmos_client import cosmos_client
from app.services.blob_storage import blob_storage
from app.routes import upload, hazop, sme_review

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: connect to Cosmos DB and create indexes
    cosmos_client.connect()
    cosmos_client.setup_indexes()
    await blob_storage.ensure_container_exists()
    yield
    # Shutdown: close connections
    cosmos_client.close()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api/upload", tags=["Upload"])
app.include_router(hazop.router, prefix="/api/hazop", tags=["HAZOP"])
app.include_router(sme_review.router, prefix="/api/review", tags=["SME Review"])


@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": settings.APP_VERSION}


@app.get("/api/health")
async def api_health():
    """Same as /health; under /api so frontend proxy can reach it."""
    return {"status": "healthy", "version": settings.APP_VERSION}
