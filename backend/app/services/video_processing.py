import json
import subprocess
from dataclasses import dataclass

from app.core.config import get_settings

settings = get_settings()


class FFprobeError(RuntimeError):
    pass


@dataclass
class VideoMetadata:
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    bitrate: int | None


def _parse_fps(rate: str | None) -> float | None:
    if not rate:
        return None
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            num_f, den_f = float(num), float(den)
            return round(num_f / den_f, 3) if den_f else None
        except ValueError:
            return None
    try:
        return float(rate)
    except ValueError:
        return None


def extract_video_metadata(file_path: str) -> VideoMetadata:
    """Run ffprobe on a video file and return duration, resolution, fps, codecs.

    Raises FFprobeError if the file cannot be probed (e.g. not a valid video).
    """
    cmd = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except FileNotFoundError as exc:
        raise FFprobeError(f"ffprobe binary not found: {settings.ffprobe_binary}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFprobeError("ffprobe timed out") from exc

    if result.returncode != 0:
        raise FFprobeError(f"ffprobe failed: {result.stderr.strip()}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FFprobeError("ffprobe returned invalid JSON") from exc

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video_stream is None:
        raise FFprobeError("No video stream found in file")

    duration = fmt.get("duration") or video_stream.get("duration")
    bitrate = fmt.get("bit_rate")

    return VideoMetadata(
        duration_seconds=round(float(duration), 3) if duration else None,
        width=video_stream.get("width"),
        height=video_stream.get("height"),
        fps=_parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
        video_codec=video_stream.get("codec_name"),
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        bitrate=int(bitrate) if bitrate else None,
    )
