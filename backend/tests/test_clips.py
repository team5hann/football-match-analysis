import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.clips import build_event_clip, build_highlights


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_event_clip_and_highlights_are_non_empty(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
        capture_output=True,
    )

    clip = tmp_path / "clip.mp4"
    build_event_clip(source, 1.0, 0.5, 0.5, clip, 2.0)
    assert clip.stat().st_size > 0

    events = [
        SimpleNamespace(event_type="shot", timestamp_seconds=0.5, xg=0.8, confidence=0.8),
        SimpleNamespace(event_type="pass", timestamp_seconds=1.5, xg=None, confidence=0.7),
    ]
    highlights, temp_dir = build_highlights(source, events, 2.0, 2)
    assert highlights.stat().st_size > 0
    shutil.rmtree(temp_dir)
    assert not temp_dir.exists()