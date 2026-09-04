import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import get_settings


class ClipError(RuntimeError):
    pass


def _run_ffmpeg(command: list[str]) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise ClipError(f"FFmpeg could not be started: {exc}") from exc
    if result.returncode != 0:
        raise ClipError(result.stderr[-1000:] or "FFmpeg failed")


def _clip_window(timestamp: float, before: float, after: float, video_duration: float | None) -> tuple[float, float]:
    start = max(0.0, timestamp - before)
    end = timestamp + after
    if video_duration is not None:
        end = min(end, video_duration)
    duration = end - start
    if duration <= 0:
        raise ClipError("Event timestamp is outside the video")
    return start, duration


def build_event_clip(
    source: Path,
    timestamp: float,
    before: float,
    after: float,
    output: Path,
    video_duration: float | None = None,
    normalize: bool = False,
) -> None:
    """Create a bounded clip, using fast stream copy before a reliable re-encode fallback."""
    if not source.is_file():
        raise ClipError("Original video file is missing")
    start, duration = _clip_window(timestamp, before, after, video_duration)
    settings = get_settings()
    output.parent.mkdir(parents=True, exist_ok=True)

    copy_command = [
        settings.ffmpeg_binary, "-y", "-ss", f"{start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}", "-map", "0:v:0?", "-map", "0:a:0?",
        "-c", "copy", "-movflags", "+faststart", str(output),
    ]
    if not normalize:
        try:
            _run_ffmpeg(copy_command)
            if output.is_file() and output.stat().st_size > 0:
                return
        except ClipError:
            pass
        output.unlink(missing_ok=True)

    encode_command = [
        settings.ffmpeg_binary, "-y", "-ss", f"{start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}", "-map", "0:v:0?", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart", str(output),
    ]
    _run_ffmpeg(encode_command)
    if not output.is_file() or output.stat().st_size == 0:
        raise ClipError("FFmpeg produced an empty clip")


def select_highlight_events(events, limit: int):
    return sorted(
        events,
        key=lambda event: (
            event.event_type == "shot",
            event.xg if event.event_type == "shot" else event.confidence or 0,
        ),
        reverse=True,
    )[:limit]


def build_highlights(source: Path, events, video_duration: float | None, limit: int) -> tuple[Path, Path]:
    selected = select_highlight_events(events, limit)
    if not selected:
        raise ClipError("No events available for highlights")

    temp_dir = Path(tempfile.mkdtemp(prefix="football-highlights-"))
    try:
        clip_paths: list[Path] = []
        for index, event in enumerate(selected):
            clip_path = temp_dir / f"clip-{index:02d}.mp4"
            build_event_clip(source, event.timestamp_seconds, 3.0, 3.0, clip_path, video_duration, normalize=True)
            clip_paths.append(clip_path)

        concat_file = temp_dir / "concat.txt"
        concat_file.write_text("\n".join(f"file '{path.as_posix()}'" for path in clip_paths) + "\n", encoding="utf-8")
        output = temp_dir / "highlights.mp4"
        settings = get_settings()
        _run_ffmpeg([
            settings.ffmpeg_binary, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c", "copy", "-movflags", "+faststart", str(output),
        ])
        if not output.is_file() or output.stat().st_size == 0:
            raise ClipError("FFmpeg produced an empty highlights video")
        return output, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise