from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.routers import clips, matches, players, teams, videos

settings = get_settings()

app = FastAPI(title="Football Match Video Analysis API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(teams.router)
app.include_router(players.router)
app.include_router(matches.router)
app.include_router(videos.router)
app.include_router(clips.router)

app.mount("/media", StaticFiles(directory=settings.video_storage_dir), name="media")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
