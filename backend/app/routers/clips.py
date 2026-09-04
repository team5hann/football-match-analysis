import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.event import Event
from app.models.match import Match
from app.models.video import Video
from app.schemas.clips import ClipEventRead
from app.services.clips import ClipError, build_event_clip, build_highlights

router = APIRouter(tags=["clips"])
EVENT_TYPES = ("shot", "pass", "possession_loss")


def _video_for_event(event: Event, db: Session) -> Video:
    video = db.get(Video, event.video_id) if event.video_id else None
    if video is None:
        video = db.scalar(select(Video).where(Video.match_id == event.match_id).order_by(Video.id))
    if video is None:
        raise HTTPException(status_code=400, detail="No source video found for event")
    return video


def _temporary_file_cleanup(path: Path) -> None:
    path.unlink(missing_ok=True)


@router.get("/api/events/{event_id}/clip")
def get_event_clip(
    event_id: int,
    background_tasks: BackgroundTasks,
    before_seconds: float = Query(3.0, ge=0, le=30),
    after_seconds: float = Query(3.0, ge=0, le=30),
    db: Session = Depends(get_db),
) -> FileResponse:
    event = db.get(Event, event_id)
    if event is None or event.event_type not in EVENT_TYPES:
        raise HTTPException(status_code=404, detail="Clip event not found")
    video = _video_for_event(event, db)
    descriptor, output_name = tempfile.mkstemp(prefix="football-clip-", suffix=".mp4")
    os.close(descriptor)
    output = Path(output_name)
    try:
        build_event_clip(Path(video.file_path), event.timestamp_seconds, before_seconds, after_seconds, output, video.duration_seconds)
    except ClipError as exc:
        output.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(_temporary_file_cleanup, output)
    return FileResponse(output, media_type="video/mp4", filename=f"event-{event.id}.mp4", background=background_tasks)


@router.get("/api/matches/{match_id}/clips", response_model=list[ClipEventRead])
def list_match_clips(
    match_id: int,
    category: str | None = Query(None, pattern="^(shot|pass|possession_loss)$"),
    db: Session = Depends(get_db),
) -> list[ClipEventRead]:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    query = select(Event).where(Event.match_id == match_id, Event.event_type.in_(EVENT_TYPES)).order_by(Event.timestamp_seconds, Event.id)
    if category:
        query = query.where(Event.event_type == category)
    return [
        ClipEventRead(
            event_id=event.id,
            event_type=event.event_type,
            timestamp_seconds=event.timestamp_seconds,
            xg=event.xg,
            confidence=event.confidence,
            track_id=event.track_id,
            description=event.description,
        )
        for event in db.scalars(query).all()
    ]


@router.get("/api/matches/{match_id}/highlights")
def get_highlights(
    match_id: int,
    background_tasks: BackgroundTasks,
    limit: int = Query(8, ge=1, le=10),
    db: Session = Depends(get_db),
) -> FileResponse:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    video = db.scalar(select(Video).where(Video.match_id == match_id).order_by(Video.id))
    events = db.scalars(select(Event).where(Event.match_id == match_id, Event.event_type.in_(EVENT_TYPES))).all()
    if video is None or not events:
        raise HTTPException(status_code=404, detail="No video events available for highlights")
    try:
        output, temp_dir = build_highlights(Path(video.file_path), events, video.duration_seconds, limit)
    except ClipError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(shutil.rmtree, temp_dir, ignore_errors=True)
    return FileResponse(output, media_type="video/mp4", filename=f"match-{match_id}-highlights.mp4", background=background_tasks)