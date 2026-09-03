import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models.enums import MatchStatus, VideoStatus
from app.models.match import Match
from app.models.video import Video
from app.schemas.video import VideoRead
from app.services.video_processing import FFprobeError, extract_video_metadata

router = APIRouter(tags=["videos"])
settings = get_settings()

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}


@router.post("/api/matches/{match_id}/video", response_model=VideoRead, status_code=201)
def upload_match_video(match_id: int, file: UploadFile, db: Session = Depends(get_db)) -> Video:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    original_name = file.filename or "upload"
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{extension}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    stored_filename = f"{uuid.uuid4().hex}{extension}"
    destination = settings.video_storage_dir / stored_filename

    size_bytes = 0
    try:
        with destination.open("wb") as out_file:
            while chunk := file.file.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > settings.max_upload_size_bytes:
                    raise HTTPException(status_code=413, detail="File exceeds maximum upload size")
                out_file.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to save file: {exc}") from exc
    finally:
        file.file.close()

    video = Video(
        match_id=match_id,
        original_filename=original_name,
        stored_filename=stored_filename,
        file_path=str(destination),
        file_size_bytes=size_bytes,
        content_type=file.content_type,
        status=VideoStatus.UPLOADED,
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    try:
        metadata = extract_video_metadata(str(destination))
    except FFprobeError as exc:
        video.status = VideoStatus.FAILED
        video.error_message = str(exc)
        db.commit()
        db.refresh(video)
        return video

    video.duration_seconds = metadata.duration_seconds
    video.width = metadata.width
    video.height = metadata.height
    video.fps = metadata.fps
    video.video_codec = metadata.video_codec
    video.audio_codec = metadata.audio_codec
    video.bitrate = metadata.bitrate
    video.status = VideoStatus.METADATA_EXTRACTED
    match.status = MatchStatus.UPLOADED
    db.commit()
    db.refresh(video)

    return video


@router.get("/api/videos/{video_id}", response_model=VideoRead)
def get_video(video_id: int, db: Session = Depends(get_db)) -> Video:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@router.delete("/api/videos/{video_id}", status_code=204)
def delete_video(video_id: int, db: Session = Depends(get_db)) -> None:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    file_path = Path(video.file_path)
    db.delete(video)
    db.commit()
    if file_path.exists():
        file_path.unlink(missing_ok=True)
