# Football Match Video Analysis

An AI-powered football match video analysis platform. Upload a match recording and
(eventually) get automatic player/ball detection, event recognition, tactical
statistics, heatmaps, auto-generated clips, and an AI coach — built up in phases.

This repo currently implements **Phase 4: basic match analysis** — YOLOv8 per-frame
detections, kit-color clustering, CPU-only jersey-number OCR, sparse tracking,
possession, movement estimates, and basic pass/loss events.

## Tech stack

- **Backend**: FastAPI (Python), SQLAlchemy, Alembic
- **Database**: PostgreSQL
- **Frontend**: Next.js (App Router, TypeScript, Tailwind CSS)
- **Video processing**: FFmpeg / ffprobe, Ultralytics YOLOv8, EasyOCR (CPU)

## Project structure

```
backend/
  app/
    core/       # settings, DB session
    models/     # SQLAlchemy models: Team, Player, Match, Video, Detection, Event, Clip
    schemas/    # Pydantic request/response schemas
    routers/    # FastAPI routers: teams, players, matches, videos
    services/   # video_processing.py, detection.py, enrichment.py
  alembic/      # DB migrations
  tests/        # pytest suite (full upload flow, validation)
frontend/
  app/          # Next.js App Router pages (match list, new match, match detail)
  components/   # StatusBadge, VideoUploadPanel
  lib/          # api.ts (typed API client), format.ts (display helpers)
docker-compose.yml
```

## Data model

- **teams**, **players** — basic roster data
- **matches** — home/away team, competition, date, status
  (`pending` → `uploaded` → `processing` → `analyzed` / `failed`)
- **videos** — one or more video files per match, with metadata extracted by
  ffprobe on upload (duration, resolution, fps, codecs, file size)
- **detections** — sampled-frame player/ball bounding boxes, confidence, and timestamp
- **team_cluster_assignments** — manual Home/Away/referee labels for color clusters
- **match_analysis_summaries**, **player_metrics** — estimated possession and
  track-level touches, distance, and speed
- **events** — estimated pass and possession-loss moments; shot detection is
  intentionally deferred until pitch/goal geometry exists

## Running with Docker Compose

The simplest way to run everything (Postgres + backend + frontend):

```bash
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API docs: http://localhost:8000/docs
- Postgres: localhost:5432 (user/password/db: `football`/`football`/`football_analysis`)

Uploaded videos persist in the `video_storage` Docker volume; Postgres data in
`postgres_data`.

## Running locally without Docker

### Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL 16 (running locally)
- FFmpeg (`ffmpeg` and `ffprobe` on your `PATH`)

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # or requirements.txt for prod-only deps

cp .env.example .env   # adjust DATABASE_URL etc. if needed

# create the database once, e.g.:
#   createuser football --pwprompt
#   createdb football_analysis -O football

alembic upgrade head
uvicorn app.main:app --reload
```

The API is now at http://localhost:8000 (interactive docs at `/docs`).

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # points NEXT_PUBLIC_API_URL at the backend
npm run dev
```

The app is now at http://localhost:3000.

### Tests

```bash
cd backend
# create a separate test database once:
#   createdb football_analysis_test -O football
source .venv/bin/activate
pytest
```

## Roadmap

- **Phase 1** — upload, store, and play back match video; CRUD for
  teams/players/matches; ffprobe metadata extraction.
- **Phase 2** — YOLOv8 player/ball detection sampled at one frame per
  second, storing per-frame detections and showing counts in the match detail view.
- **Phase 3 (this repo)** — torso RGB clustering, CPU EasyOCR jersey numbers, and
  manual cluster/team and jersey corrections.
- **Phase 4 (this repo)** — possession, touches, rough distance/speed, sparse
  IoU tracking, and pass/loss events from stored detections. Distances use a
  rough 105x68 m scale without homography; shot detection remains a TODO.
- **Phase 5a (this repo)** — team/player movement heatmaps from stored
  detections. Heatmap coordinates are approximate camera coordinates, not a
  calibrated pitch projection.
- **Phase 5b (this repo)** — Home/Away passing networks derived from stored pass
  events and possession history. Nodes and edges use approximate camera
  coordinates and sparse event data.
- **Phase 5c (this repo)** — rough Home/Away formation estimates, team width,
  depth, compactness, and average player positions from stored detections.
  These metrics use whole-video camera coordinates and do not model formation
  changes over time.
- **Phase 5+** — xG,
  automatic clip and highlight generation, AI Coach, report export.

See the original product specification (in the project's build brief) for the
full long-term feature set.
