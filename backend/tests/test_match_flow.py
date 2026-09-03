from pathlib import Path


def test_full_upload_flow(client, tmp_path):
    home = client.post("/api/teams", json={"name": "FC Academy"}).json()
    away = client.post("/api/teams", json={"name": "FC Opponent"}).json()

    match = client.post(
        "/api/matches",
        json={"home_team_id": home["id"], "away_team_id": away["id"], "competition": "Test Cup"},
    ).json()
    assert match["status"] == "pending"

    video_path = _make_test_video(tmp_path)
    with video_path.open("rb") as f:
        resp = client.post(
            f"/api/matches/{match['id']}/video",
            files={"file": ("clip.mp4", f, "video/mp4")},
        )
    assert resp.status_code == 201, resp.text
    video = resp.json()
    assert video["status"] == "metadata_extracted"
    assert video["width"] == 320
    assert video["height"] == 240
    assert video["duration_seconds"] and video["duration_seconds"] > 0
    assert video["stream_url"].startswith("/media/")

    detail = client.get(f"/api/matches/{match['id']}").json()
    assert detail["status"] == "uploaded"
    assert len(detail["videos"]) == 1
    assert detail["videos"][0]["stream_url"] == video["stream_url"]


def test_upload_rejects_bad_extension(client):
    match = client.post("/api/matches", json={}).json()
    resp = client.post(
        f"/api/matches/{match['id']}/video",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


def test_upload_missing_match_returns_404(client):
    resp = client.post(
        "/api/matches/999999/video",
        files={"file": ("clip.mp4", b"fake", "video/mp4")},
    )
    assert resp.status_code == 404


def _make_test_video(tmp_path: Path) -> Path:
    import subprocess

    out = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out
