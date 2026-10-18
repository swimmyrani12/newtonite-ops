import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.app.config import settings
from backend.app.database import engine, Base
from backend.app.routes import auth, teams, items, dashboard
from backend.app.seed import seed_database

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes tables and seeds data on application startup."""
    print("Initializing database tables...")
    Base.metadata.create_all(bind=engine)
    # Automatically seed if database is fresh
    try:
        seed_database(total_items=250)
    except Exception as e:
        print(f"Seed note: {e}")
    yield

app = FastAPI(
    title="Newtonite Operations Under Pressure",
    description="Internal Web Application to create, assign, track, and resolve operational work items across teams.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(auth.router)
app.include_router(teams.router)
app.include_router(items.router)
app.include_router(dashboard.router)

@app.get("/api/health", tags=["System"])
def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "database": "connected"
    }

# Mount static frontend directory
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.get("/", include_in_schema=False)
    def serve_frontend():
        return FileResponse(os.path.join(frontend_dir, "index.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
