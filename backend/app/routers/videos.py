import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models.enums import MatchStatus, VideoStatus
from app.models.match import Match
from app.models.video import Video
from app.schemas.video import VideoRead
from app.schemas.detection import DetectionRead, DetectionStatus
from app.schemas.homography import HomographyStatus
from app.services.detection import list_detections, reset_detection_data, run_detection
from app.services.enrichment import run_enrichment
from app.services.homography import read_homography_status, run_pitch_homography
from app.models.detection import Detection
from app.schemas.detection import DetectionUpdate
from app.services.video_processing import FFprobeError, extract_video_metadata

router = APIRouter(tags=["videos"])
settings = get_settings()

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}


def _status_value(status: VideoStatus | str) -> str:
    return getattr(status, "value", status)


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


@router.post("/api/videos/{video_id}/detection", response_model=DetectionStatus, status_code=202)
def start_detection(video_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> DetectionStatus:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status == VideoStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Detection is already running")
    video.status = VideoStatus.PROCESSING
    video.match.status = MatchStatus.PROCESSING
    db.commit()
    background_tasks.add_task(run_detection, video_id)
    return DetectionStatus(video_id=video_id, status=_status_value(video.status), detections_count=0, detections=[])


@router.post("/api/videos/{video_id}/detection/regenerate", response_model=DetectionStatus, status_code=202)
def regenerate_detection(
    video_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
) -> DetectionStatus:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status == VideoStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Processing is already running")

    reset_detection_data(video_id, db)
    video.status = VideoStatus.PROCESSING
    video.match.status = MatchStatus.PROCESSING
    db.commit()
    background_tasks.add_task(run_detection, video_id)
    return DetectionStatus(video_id=video_id, status=_status_value(video.status), detections_count=0, detections=[])


@router.get("/api/videos/{video_id}/detection", response_model=DetectionStatus)
def get_detection_status(video_id: int, db: Session = Depends(get_db)) -> DetectionStatus:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    detections = list_detections(video_id, db)
    return DetectionStatus(
        video_id=video_id,
        status=_status_value(video.status),
        detections_count=len(detections),
        detections=[DetectionRead.model_validate(detection) for detection in detections],
    )


@router.post("/api/videos/{video_id}/enrichment", response_model=DetectionStatus, status_code=202)
def start_enrichment(video_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> DetectionStatus:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status == VideoStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Processing is already running")
    if not db.query(Detection).filter(Detection.video_id == video_id, Detection.class_name == "player").first():
        raise HTTPException(status_code=400, detail="Run player detection before enrichment")
    video.status = VideoStatus.PROCESSING
    video.match.status = MatchStatus.PROCESSING
    db.commit()
    background_tasks.add_task(run_enrichment, video_id)
    return DetectionStatus(video_id=video_id, status=_status_value(video.status), detections_count=0, detections=[])


def _run_pitch_homography_task(video_id: int) -> None:
    try:
        run_pitch_homography(video_id)
    except Exception as exc:  # background task - log, don't crash the worker
        print(f"[homography] video {video_id} failed: {exc}", flush=True)


@router.post("/api/videos/{video_id}/pitch-homography", response_model=HomographyStatus, status_code=202)
def start_pitch_homography(
    video_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
) -> HomographyStatus:
    """Compute a per-frame image->pitch homography and project every detection.

    Extra pass on top of detection: it does not re-run object detection but it
    DOES re-decode the video frames and run a large pose model, so it is not
    free. Runs in the background; poll GET for progress.
    """
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if not db.query(Detection).filter(Detection.video_id == video_id).first():
        raise HTTPException(status_code=400, detail="Run detection before pitch homography")
    background_tasks.add_task(_run_pitch_homography_task, video_id)
    return HomographyStatus(**read_homography_status(db, video_id))


@router.get("/api/videos/{video_id}/pitch-homography", response_model=HomographyStatus)
def get_pitch_homography_status(video_id: int, db: Session = Depends(get_db)) -> HomographyStatus:
    if db.get(Video, video_id) is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return HomographyStatus(**read_homography_status(db, video_id))


@router.patch("/api/detections/{detection_id}", response_model=DetectionRead)
def update_detection(detection_id: int, payload: DetectionUpdate, db: Session = Depends(get_db)) -> Detection:
    detection = db.get(Detection, detection_id)
    if detection is None:
        raise HTTPException(status_code=404, detail="Detection not found")
    detection.jersey_number = payload.jersey_number
    detection.jersey_number_confidence = 1.0 if payload.jersey_number is not None else None
    db.commit()
    db.refresh(detection)
    return detection


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
