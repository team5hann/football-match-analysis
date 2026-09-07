"""Whole-match player identity layer.

Tracking (Phase 4) produces many short ``track_id`` segments per real player,
because the tracker restarts whenever it loses continuity (player leaves frame,
occlusion, ...). This module stitches those segments back together into
``match_players`` using a **colour + jersey-number heuristic**:

* two segments are the same player only if they share the same
  ``team_color_cluster`` AND the same jersey number that was read by OCR with
  at least ``JERSEY_CONFIDENCE_THRESHOLD`` confidence on at least one frame;
* a segment without such a confident number is kept as its own
  ``is_unknown`` identity labelled ``"Unknown #<track_id>"`` and is never
  merged with anything.

This is explicitly NOT visual re-identification. Accuracy is bounded by OCR
quality and how often numbers are actually legible, so many real players will
still be split into several ``Unknown`` identities.
"""
from collections import Counter, defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.detection import Detection
from app.models.match import Match
from app.models.player_identity import MatchPlayer, TrackPlayerLink
from app.models.video import Video

# A jersey number is only trusted for stitching two tracks together once OCR
# read it, above this confidence, enough times AND with that number clearly
# dominating the *other* numbers OCR produced for the same track. The share is
# taken over the track's confident reads, not its frames: an earlier
# frame-fraction rule assumed short IoU tracker fragments, but ByteTrack tracks
# span the whole clip, so `0.15 * frames` became hundreds of reads and no track
# could ever clear it. OCR on this footage is heavily biased toward a few
# numbers, so a scattered 3-4 reads is still treated as noise.
JERSEY_CONFIDENCE_THRESHOLD = 0.5
JERSEY_MIN_READ_SHARE = 0.6
JERSEY_MIN_READ_COUNT = 5


class TrackIdentity:
    """One resolved identity plus the track segments that feed it."""

    def __init__(
        self,
        track_ids: list[int],
        team_color_cluster: int | None,
        jersey_number: int | None,
        confidence: float,
        is_unknown: bool,
    ) -> None:
        self.track_ids = track_ids
        self.team_color_cluster = team_color_cluster
        self.jersey_number = jersey_number
        self.confidence = confidence
        self.is_unknown = is_unknown


def _mode(values) -> int | None:
    counter = Counter(value for value in values if value is not None)
    return counter.most_common(1)[0][0] if counter else None


def summarise_track(
    detections: list[dict],
    jersey_confidence_threshold: float = JERSEY_CONFIDENCE_THRESHOLD,
    min_read_count: int = JERSEY_MIN_READ_COUNT,
    min_read_share: float = JERSEY_MIN_READ_SHARE,
) -> tuple[int | None, int | None, float]:
    """Reduce one track's detections to (cluster, confident number, confidence).

    ``cluster`` is the most common ``team_color_cluster`` seen on the track.
    ``number`` is the most common jersey number among reads that cleared the
    confidence threshold (mirrors the Phase 5a mode logic), or ``None``.
    ``confidence`` is the mean confidence of the reads that voted for ``number``.
    """
    cluster = _mode([detection.get("team_color_cluster") for detection in detections])
    confident_reads = [
        detection
        for detection in detections
        if detection.get("jersey_number") is not None
        and detection.get("jersey_number_confidence") is not None
        and detection["jersey_number_confidence"] >= jersey_confidence_threshold
    ]
    number = _mode([detection["jersey_number"] for detection in confident_reads])
    if number is None:
        return cluster, None, 0.0
    scores = [
        detection["jersey_number_confidence"]
        for detection in confident_reads
        if detection["jersey_number"] == number
    ]
    # Trust the number only if OCR read it enough times AND it clearly dominates
    # the other numbers OCR produced for this track - a scattered vote across
    # several numbers is misread noise, not a legible jersey.
    if len(scores) < min_read_count:
        return cluster, None, 0.0
    if len(scores) / len(confident_reads) < min_read_share:
        return cluster, None, 0.0
    return cluster, number, round(sum(scores) / len(scores), 4)


def group_tracks_into_identities(
    track_detections: dict[int, list[dict]],
    jersey_confidence_threshold: float = JERSEY_CONFIDENCE_THRESHOLD,
    min_read_count: int = JERSEY_MIN_READ_COUNT,
    min_read_share: float = JERSEY_MIN_READ_SHARE,
    track_frames: dict[int, set[float]] | None = None,
) -> list[TrackIdentity]:
    """Heuristically group tracks that share cluster + confident jersey number.

    Tracks that lack a team colour cluster or a confident jersey number are
    returned as standalone ``is_unknown`` identities (one per track) - they
    cannot be merged safely without real re-identification.

    When ``track_frames`` (``{track_id: set of frame timestamps}``) is given,
    two tracks that carry the same (cluster, number) but are on the pitch at the
    same time are treated as an OCR collision: at most one can really be that
    player and we cannot tell which, so every track in that group is demoted to
    ``is_unknown``. This guard matters for ByteTrack output, where tracks are
    long and genuinely concurrent (unlike the short IoU fragments this module
    was first written for, which rarely overlapped).
    """
    summaries = {
        track_id: summarise_track(detections, jersey_confidence_threshold, min_read_count, min_read_share)
        for track_id, detections in track_detections.items()
    }

    candidates: dict[tuple[int, int], list[int]] = defaultdict(list)
    unknown_track_ids: list[int] = []
    for track_id in sorted(track_detections):
        cluster, number, _confidence = summaries[track_id]
        if cluster is not None and number is not None:
            candidates[(cluster, number)].append(track_id)
        else:
            unknown_track_ids.append(track_id)

    def _tracks_overlap(track_ids: list[int]) -> bool:
        if track_frames is None:
            return False
        seen: set[float] = set()
        for track_id in track_ids:
            frames = track_frames.get(track_id) or set()
            if seen & frames:
                return True
            seen |= frames
        return False

    identities: list[TrackIdentity] = []
    for (cluster, number), track_ids in candidates.items():
        if len(track_ids) > 1 and _tracks_overlap(track_ids):
            for track_id in track_ids:
                identities.append(TrackIdentity([track_id], cluster, None, 0.0, is_unknown=True))
            continue
        confidences = [summaries[track_id][2] for track_id in track_ids]
        identities.append(
            TrackIdentity(
                sorted(track_ids),
                cluster,
                number,
                round(sum(confidences) / len(confidences), 4),
                is_unknown=False,
            )
        )

    for track_id in unknown_track_ids:
        cluster, number, confidence = summaries[track_id]
        identities.append(TrackIdentity([track_id], cluster, number, confidence, is_unknown=True))

    return identities


def _label(role: str, jersey_number: int | None, first_track_id: int) -> str:
    number_label = f"#{jersey_number}" if jersey_number is not None else f"Unknown #{first_track_id}"
    return f"{role.title()} · {number_label}"


def _serialise(
    row: MatchPlayer,
    identity: TrackIdentity,
    detection_count_by_track: dict[int, int],
    cluster_roles: dict[int, str],
) -> dict:
    role = cluster_roles.get(identity.team_color_cluster, "unknown")
    track_ids = sorted(identity.track_ids)
    return {
        "id": row.id,
        "team_color_cluster": identity.team_color_cluster,
        "team_role": role,
        "jersey_number": identity.jersey_number,
        "confidence": row.confidence,
        "is_unknown": identity.is_unknown,
        "label": _label(role, identity.jersey_number, track_ids[0]),
        "track_ids": track_ids,
        "detection_count": sum(detection_count_by_track.get(track_id, 0) for track_id in track_ids),
    }


def link_map(db: Session, match_id: int) -> dict[int, int]:
    """Return ``{track_id: match_player_id}`` for a match's current identities."""
    return {
        link.track_id: link.match_player_id
        for link in db.scalars(
            select(TrackPlayerLink).where(TrackPlayerLink.match_id == match_id)
        ).all()
    }


def merge_player_identities(
    match_id: int,
    session_factory=SessionLocal,
    db: Session | None = None,
) -> dict:
    """(Re)build ``match_players`` / ``track_player_links`` for a match.

    Idempotent: previous identity rows for the match are dropped first. Pass an
    open ``db`` session to run inside a caller's transaction (e.g. analysis);
    otherwise a session is opened and committed here.
    """
    owns_session = db is None
    if db is None:
        db = session_factory()
    try:
        match = db.get(Match, match_id)
        if match is None:
            raise ValueError("Match not found")

        video_ids = [video.id for video in db.scalars(select(Video).where(Video.match_id == match_id)).all()]
        detections = (
            db.scalars(
                select(Detection).where(
                    Detection.video_id.in_(video_ids),
                    Detection.class_name == "player",
                    Detection.track_id.is_not(None),
                )
            ).all()
            if video_ids
            else []
        )

        by_track: dict[int, list[dict]] = defaultdict(list)
        track_frames: dict[int, set[float]] = defaultdict(set)
        for detection in detections:
            by_track[detection.track_id].append(
                {
                    "team_color_cluster": detection.team_color_cluster,
                    "jersey_number": detection.jersey_number,
                    "jersey_number_confidence": detection.jersey_number_confidence,
                }
            )
            track_frames[detection.track_id].add(detection.frame_timestamp)

        identities = group_tracks_into_identities(by_track, track_frames=track_frames)

        db.execute(delete(TrackPlayerLink).where(TrackPlayerLink.match_id == match_id))
        db.execute(delete(MatchPlayer).where(MatchPlayer.match_id == match_id))
        db.flush()

        detection_count_by_track = {track_id: len(rows) for track_id, rows in by_track.items()}
        cluster_roles = {item.cluster_id: item.role for item in match.team_cluster_assignments}
        created: list[tuple[MatchPlayer, TrackIdentity]] = []
        for identity in identities:
            row = MatchPlayer(
                match_id=match_id,
                team_color_cluster=identity.team_color_cluster,
                jersey_number=identity.jersey_number,
                confidence=identity.confidence,
                is_unknown=identity.is_unknown,
            )
            db.add(row)
            db.flush()
            for track_id in identity.track_ids:
                db.add(TrackPlayerLink(match_id=match_id, track_id=track_id, match_player_id=row.id))
            created.append((row, identity))

        # Flush the last batch of links too - the session has autoflush disabled,
        # so callers that immediately query the links (e.g. run_analysis) would
        # otherwise miss the final identity's rows.
        db.flush()

        if owns_session:
            db.commit()

        players = [
            _serialise(row, identity, detection_count_by_track, cluster_roles)
            for row, identity in created
        ]
        return {
            "match_id": match_id,
            "track_count": len(by_track),
            "identified_count": sum(1 for _, identity in created if not identity.is_unknown),
            "unknown_count": sum(1 for _, identity in created if identity.is_unknown),
            "players": players,
        }
    except Exception:
        if owns_session:
            db.rollback()
        raise
    finally:
        if owns_session:
            db.close()


def read_player_identities(db: Session, match_id: int) -> dict:
    """Serialise the identities already stored for a match (no recompute)."""
    match = db.get(Match, match_id)
    if match is None:
        raise ValueError("Match not found")
    rows = db.scalars(
        select(MatchPlayer).where(MatchPlayer.match_id == match_id).order_by(MatchPlayer.id)
    ).all()
    links = db.scalars(select(TrackPlayerLink).where(TrackPlayerLink.match_id == match_id)).all()
    tracks_by_player: dict[int, list[int]] = defaultdict(list)
    for link in links:
        tracks_by_player[link.match_player_id].append(link.track_id)

    video_ids = [video.id for video in db.scalars(select(Video).where(Video.match_id == match_id)).all()]
    detection_count_by_track: dict[int, int] = {}
    if video_ids:
        for track_id, count in db.execute(
            select(Detection.track_id, func.count(Detection.id))
            .where(
                Detection.video_id.in_(video_ids),
                Detection.class_name == "player",
                Detection.track_id.is_not(None),
            )
            .group_by(Detection.track_id)
        ).all():
            detection_count_by_track[track_id] = count

    cluster_roles = {item.cluster_id: item.role for item in match.team_cluster_assignments}
    players = []
    for row in rows:
        track_ids = sorted(tracks_by_player.get(row.id, []))
        role = cluster_roles.get(row.team_color_cluster, "unknown")
        players.append(
            {
                "id": row.id,
                "team_color_cluster": row.team_color_cluster,
                "team_role": role,
                "jersey_number": row.jersey_number,
                "confidence": row.confidence,
                "is_unknown": row.is_unknown,
                "label": _label(role, row.jersey_number, track_ids[0] if track_ids else row.id),
                "track_ids": track_ids,
                "detection_count": sum(detection_count_by_track.get(track_id, 0) for track_id in track_ids),
            }
        )
    return {
        "match_id": match_id,
        "track_count": len(links),
        "identified_count": sum(1 for row in rows if not row.is_unknown),
        "unknown_count": sum(1 for row in rows if row.is_unknown),
        "players": players,
    }
