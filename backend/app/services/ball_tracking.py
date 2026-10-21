"""Turn noisy per-frame ball candidates into one clean ball trajectory.

The ball detector runs with a deliberately low confidence threshold so that a
small, motion-blurred ball in flight is still found. The price is false
positives (heads, socks, pitch markings, the penalty spot) and frames with
several candidates. Every downstream consumer (possession, passes, shots,
duels) reads ``balls[0]`` for a frame, so a single wrong box corrupts the
stats. This module keeps only the candidates that form a physically plausible
trajectory, and fills short gaps (fast flight, occlusion by a player) by
linear interpolation.

Pipeline:

1. **Tracklets** - candidates are linked frame-to-frame greedily (highest
   confidence first) to the tracklet whose constant-velocity prediction is
   nearest, within a speed gate. A tracklet survives detection gaps of up to
   ``max_gap_frames``.
2. **Selection** - there is only one ball, so tracklets must not overlap in
   time. Weighted interval scheduling picks the set of non-overlapping
   tracklets with the highest total confidence. Short low-confidence
   tracklets (one-off false positives) are discarded before that.
3. **Interpolation** - gaps inside and between selected tracklets are
   filled linearly when the implied speed is still plausible.

Everything here is pure Python on plain numbers, so it is cheap next to the
model inference and fully unit-testable without a GPU.
"""

from bisect import bisect_right
from dataclasses import dataclass, field
from math import hypot

# Fastest plausible ball movement between two consecutive sampled frames, as
# a fraction of the frame width. A 30 m/s shot at 30 fps travels 1 m per
# frame; on a wide match camera 1 m is roughly 1-3 % of the frame width, so
# 6 % leaves headroom for a tight broadcast zoom.
MAX_STEP_FRACTION = 0.06
# Tracklets shorter than this (in frames) are only kept when at least one of
# their detections is confident; isolated weak hits are almost always noise.
MIN_TRACKLET_FRAMES = 3
CONFIDENT_SINGLE_DETECTION = 0.5
# Tracklets whose mean confidence is below this are dropped even when long:
# e.g. a static pitch marking that the detector keeps hitting at 0.06.
MIN_MEAN_CONFIDENCE = 0.12


@dataclass(frozen=True)
class BallCandidate:
    frame: int
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    @property
    def size(self) -> float:
        return max(self.x2 - self.x1, self.y2 - self.y1)


@dataclass(frozen=True)
class BallPoint:
    frame: int
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    interpolated: bool = False


@dataclass
class _Tracklet:
    points: list[BallCandidate] = field(default_factory=list)

    @property
    def start(self) -> int:
        return self.points[0].frame

    @property
    def end(self) -> int:
        return self.points[-1].frame

    @property
    def score(self) -> float:
        return sum(point.confidence for point in self.points)

    def predict(self, frame: int) -> tuple[float, float]:
        last = self.points[-1]
        x, y = last.center
        if len(self.points) < 2:
            return x, y
        previous = self.points[-2]
        steps = last.frame - previous.frame
        px, py = previous.center
        vx, vy = (x - px) / steps, (y - py) / steps
        ahead = frame - last.frame
        return x + vx * ahead, y + vy * ahead


def build_tracklets(
    candidates: list[BallCandidate], *, max_step: float, max_gap_frames: int
) -> list[_Tracklet]:
    by_frame: dict[int, list[BallCandidate]] = {}
    for candidate in candidates:
        by_frame.setdefault(candidate.frame, []).append(candidate)

    active: list[_Tracklet] = []
    finished: list[_Tracklet] = []
    for frame in sorted(by_frame):
        still_active = []
        for tracklet in active:
            (still_active if frame - tracklet.end <= max_gap_frames + 1 else finished).append(tracklet)
        active = still_active

        extended: set[int] = set()
        for candidate in sorted(by_frame[frame], key=lambda c: c.confidence, reverse=True):
            best_index, best_distance = None, None
            for index, tracklet in enumerate(active):
                if index in extended:
                    continue
                steps = frame - tracklet.end
                px, py = tracklet.predict(frame)
                distance = hypot(candidate.center[0] - px, candidate.center[1] - py)
                gate = max_step * steps + 2 * candidate.size
                if distance <= gate and (best_distance is None or distance < best_distance):
                    best_index, best_distance = index, distance
            if best_index is None:
                active.append(_Tracklet([candidate]))
                extended.add(len(active) - 1)
            else:
                active[best_index].points.append(candidate)
                extended.add(best_index)
    return finished + active


def _is_plausible(tracklet: _Tracklet) -> bool:
    if tracklet.score / len(tracklet.points) < MIN_MEAN_CONFIDENCE:
        return False
    if len(tracklet.points) >= MIN_TRACKLET_FRAMES:
        return True
    return max(point.confidence for point in tracklet.points) >= CONFIDENT_SINGLE_DETECTION


def select_tracklets(tracklets: list[_Tracklet]) -> list[_Tracklet]:
    """Weighted interval scheduling: best-scoring set of time-disjoint tracklets."""
    ordered = sorted(tracklets, key=lambda t: t.end)
    ends = [t.end for t in ordered]
    best = [0.0] * (len(ordered) + 1)
    take = [False] * len(ordered)
    for i, tracklet in enumerate(ordered):
        # Number of tracklets ending strictly before this one starts.
        compatible = bisect_right(ends, tracklet.start - 1, 0, i)
        with_this = best[compatible] + tracklet.score
        take[i] = with_this > best[i]
        best[i + 1] = with_this if take[i] else best[i]

    chosen: list[_Tracklet] = []
    i = len(ordered) - 1
    while i >= 0:
        if take[i]:
            chosen.append(ordered[i])
            i = bisect_right(ends, ordered[i].start - 1, 0, i) - 1
        else:
            i -= 1
    return sorted(chosen, key=lambda t: t.start)


def _interpolate(a: BallCandidate, b: BallCandidate, max_step: float) -> list[BallPoint]:
    steps = b.frame - a.frame
    if steps <= 1:
        return []
    ax, ay = a.center
    bx, by = b.center
    if hypot(bx - ax, by - ay) / steps > max_step:
        return []
    points = []
    for offset in range(1, steps):
        t = offset / steps
        points.append(
            BallPoint(
                frame=a.frame + offset,
                x1=a.x1 + (b.x1 - a.x1) * t,
                y1=a.y1 + (b.y1 - a.y1) * t,
                x2=a.x2 + (b.x2 - a.x2) * t,
                y2=a.y2 + (b.y2 - a.y2) * t,
                confidence=0.0,
                interpolated=True,
            )
        )
    return points


def clean_ball_trajectory(
    candidates: list[BallCandidate], *, frame_width: float, max_gap_frames: int
) -> list[BallPoint]:
    """Return at most one ball per frame, sorted by frame, gaps interpolated."""
    max_step = MAX_STEP_FRACTION * frame_width
    tracklets = build_tracklets(candidates, max_step=max_step, max_gap_frames=max_gap_frames)
    chosen = select_tracklets([t for t in tracklets if _is_plausible(t)])

    detected = [point for tracklet in chosen for point in tracklet.points]
    result: list[BallPoint] = []
    for index, point in enumerate(detected):
        result.append(BallPoint(point.frame, point.x1, point.y1, point.x2, point.y2, point.confidence))
        if index + 1 < len(detected):
            following = detected[index + 1]
            if following.frame - point.frame <= max_gap_frames + 1:
                result.extend(_interpolate(point, following, max_step))
    return result
