FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ByteTrack (used by Ultralytics model.track) needs the linear-assignment solver.
# Kept as its own layer so it doesn't bust the big requirements/torch cache.
RUN pip install --no-cache-dir lap==0.5.13

COPY . .

RUN mkdir -p /app/models && \
    gdown --id 1isw4wx-MK9h9LMr36VvIWlJD6ppUvw7V \
    -O /app/models/football-ball-detection.pt

# Pitch keypoint model: fine-tuned YOLOv8x-pose, 32 pitch keypoints, from
# https://huggingface.co/martinjolif/yolo-football-pitch-detection (AGPL-3.0).
# Same idea as the ball model - a single downloaded weight, loaded with YOLO().
RUN pip install --no-cache-dir "huggingface_hub[hf_transfer]" && \
    python -c "from huggingface_hub import hf_hub_download; \
hf_hub_download('martinjolif/yolo-football-pitch-detection', 'yolo-football-pitch-detection.pt', \
local_dir='/app/models')" && \
    mv /app/models/yolo-football-pitch-detection.pt /app/models/football-pitch-detection.pt

# Player detector (YOLO11 medium, COCO person class). Downloaded at build time so
# the first analysis does not stall on it; PLAYER_MODEL overrides the choice.
RUN python -c "from ultralytics import YOLO; YOLO('yolo11m.pt')"

RUN mkdir -p /app/storage/videos

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
