from app.services.player_identity import (
    JERSEY_CONFIDENCE_THRESHOLD,
    group_tracks_into_identities,
    summarise_track,
)


def _det(cluster, number, confidence):
    return {
        "team_color_cluster": cluster,
        "jersey_number": number,
        "jersey_number_confidence": confidence,
    }


def test_two_tracks_same_colour_and_number_merge_into_one_identity():
    tracks = {
        1: [_det(0, 9, 0.9), _det(0, 9, 0.8), _det(0, 9, 0.85)],
        2: [_det(0, 9, 0.7), _det(0, 9, 0.95)],
        3: [_det(1, 4, 0.9), _det(1, 4, 0.88)],  # different colour + number -> its own identity
    }

    identities = group_tracks_into_identities(tracks, min_read_count=1, min_read_share=0.0)

    merged = [identity for identity in identities if set(identity.track_ids) == {1, 2}]
    assert len(merged) == 1
    assert merged[0].team_color_cluster == 0
    assert merged[0].jersey_number == 9
    assert merged[0].is_unknown is False
    assert 0 < merged[0].confidence <= 1

    other = [identity for identity in identities if identity.track_ids == [3]]
    assert len(other) == 1 and other[0].jersey_number == 4 and other[0].is_unknown is False


def test_track_without_confident_number_stays_separate_as_unknown():
    tracks = {
        10: [_det(0, 7, 0.95), _det(0, 7, 0.9)],           # confident #7
        11: [_det(0, 7, 0.2), _det(0, None, None)],        # only low-confidence reads
        12: [_det(0, None, None), _det(0, None, None)],    # no number at all
    }

    identities = group_tracks_into_identities(tracks, min_read_count=1, min_read_share=0.0)
    by_track = {tuple(sorted(identity.track_ids)): identity for identity in identities}

    assert (10,) in by_track and by_track[(10,)].is_unknown is False
    # 11 and 12 have no trustworthy number -> each its own Unknown identity,
    # never merged with track 10 or with each other.
    assert (11,) in by_track and by_track[(11,)].is_unknown is True
    assert by_track[(11,)].jersey_number is None
    assert (12,) in by_track and by_track[(12,)].is_unknown is True
    assert (10, 11) not in by_track and (11, 12) not in by_track


def test_same_number_but_different_colour_does_not_merge():
    tracks = {
        1: [_det(0, 5, 0.9)],
        2: [_det(1, 5, 0.9)],
    }

    identities = group_tracks_into_identities(tracks, min_read_count=1, min_read_share=0.0)

    assert len(identities) == 2
    assert all(len(identity.track_ids) == 1 for identity in identities)


def test_summarise_track_uses_confidence_threshold_and_mode():
    detections = [
        _det(2, 11, 0.9),
        _det(2, 11, 0.8),
        _det(2, 23, 0.9),                       # a competing but rarer confident read
        _det(2, 99, JERSEY_CONFIDENCE_THRESHOLD - 0.01),  # below threshold, ignored
        _det(None, None, None),
    ]

    cluster, number, confidence = summarise_track(detections, min_read_count=1, min_read_share=0.0)

    assert cluster == 2
    assert number == 11
    assert confidence == round((0.9 + 0.8) / 2, 4)


def test_no_tracks_yields_no_identities():
    assert group_tracks_into_identities({}) == []


def test_number_read_too_few_times_is_not_trusted():
    # "22" read confidently only 3 times - below the absolute count floor, so
    # the track carries no trusted number regardless of how it dominates.
    detections = [_det(0, 22, 0.9)] * 3 + [_det(0, None, None)] * 37
    cluster, number, _confidence = summarise_track(detections, min_read_count=4, min_read_share=0.25)
    assert cluster == 0 and number is None

    # same three reads, now above the count floor and unanimous among the
    # confident reads -> trusted.
    cluster, number, _confidence = summarise_track(
        [_det(0, 22, 0.9)] * 3 + [_det(0, None, None)], min_read_count=3, min_read_share=0.25
    )
    assert number == 22


def test_number_that_does_not_dominate_confident_reads_is_not_trusted():
    # 10 confident reads: 5x "16", 4x "22", 1x "7" - "16" wins the mode but is
    # only half the reads, below the 0.6 agreement share -> not trusted.
    detections = [_det(0, 16, 0.9)] * 5 + [_det(0, 22, 0.9)] * 4 + [_det(0, 7, 0.9)]
    cluster, number, _confidence = summarise_track(detections, min_read_count=3, min_read_share=0.6)
    assert cluster == 0 and number is None


def test_same_number_on_time_overlapping_tracks_is_an_ocr_collision():
    # two tracks, same cluster + confident "9", but both live on frames 0-2:
    # they cannot be the same player, so neither keeps the number.
    tracks = {
        1: [_det(0, 9, 0.9)] * 3,
        2: [_det(0, 9, 0.9)] * 3,
    }
    frames = {1: {0.0, 0.1, 0.2}, 2: {0.0, 0.1, 0.2}}
    identities = group_tracks_into_identities(
        tracks, min_read_count=1, min_read_share=0.0, track_frames=frames
    )
    assert len(identities) == 2
    assert all(identity.is_unknown and identity.jersey_number is None for identity in identities)

    # disjoint in time -> the collision guard does not fire, they merge.
    frames_disjoint = {1: {0.0, 0.1, 0.2}, 2: {5.0, 5.1, 5.2}}
    merged = group_tracks_into_identities(
        tracks, min_read_count=1, min_read_share=0.0, track_frames=frames_disjoint
    )
    assert len(merged) == 1 and sorted(merged[0].track_ids) == [1, 2]
    assert merged[0].is_unknown is False and merged[0].jersey_number == 9


# --- endpoint / DB integration -------------------------------------------------

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.detection import Detection  # noqa: E402
from app.models.match import Match  # noqa: E402
from app.models.video import Video  # noqa: E402

_SessionLocal = sessionmaker(bind=create_engine(get_settings().database_url), autoflush=False)


def _seed_match_with_tracks(track_specs: dict[int, list[tuple]]) -> int:
    """track_specs: {track_id: [(cluster, jersey_number, confidence), ...]}"""
    session = _SessionLocal()
    try:
        match = Match()
        session.add(match)
        session.flush()
        video = Video(
            match_id=match.id,
            original_filename="c.mp4",
            stored_filename="c.mp4",
            file_path="/tmp/c.mp4",
            width=1920,
            height=1080,
        )
        session.add(video)
        session.flush()
        timestamp = 0.0
        for track_id, reads in track_specs.items():
            for cluster, number, confidence in reads:
                session.add(
                    Detection(
                        video_id=video.id,
                        frame_timestamp=timestamp,
                        bounding_box={"x": 1, "y": 1, "width": 10, "height": 20},
                        class_name="player",
                        confidence=0.9,
                        track_id=track_id,
                        team_color_cluster=cluster,
                        jersey_number=number,
                        jersey_number_confidence=confidence,
                    )
                )
                timestamp = round(timestamp + 0.1, 3)
        session.commit()
        return match.id
    finally:
        session.close()


def test_player_identities_endpoint_merges_and_reports_counts(client):
    match_id = _seed_match_with_tracks(
        {
            # >= JERSEY_MIN_READ_COUNT confident reads so the number is trusted
            1: [(0, 9, 0.9)] * 6,
            2: [(0, 9, 0.8)] * 6,        # same colour + number as track 1 -> merge
            3: [(1, 4, 0.9)] * 6,        # distinct identified player
            4: [(1, None, None)] * 3,    # no number -> Unknown, separate
            5: [(0, 9, 0.9)] + [(0, None, None)] * 20,  # 1 "#9" read on a long track -> not trusted
        }
    )

    response = client.post(f"/api/matches/{match_id}/player-identities")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["track_count"] == 5
    assert body["identified_count"] == 2  # {cluster0 #9}, {cluster1 #4}
    assert body["unknown_count"] == 2     # track 4 and track 5 (number not legible enough)

    merged = next(player for player in body["players"] if player["jersey_number"] == 9)
    assert sorted(merged["track_ids"]) == [1, 2]
    assert merged["is_unknown"] is False
    assert merged["detection_count"] == 12

    unknown_tracks = sorted(
        track for player in body["players"] if player["is_unknown"] for track in player["track_ids"]
    )
    assert unknown_tracks == [4, 5]

    # GET returns the same stored result without recomputing.
    again = client.get(f"/api/matches/{match_id}/player-identities")
    assert again.status_code == 200
    assert again.json()["identified_count"] == 2
    assert again.json()["unknown_count"] == 2


def test_player_identities_endpoint_requires_analysis(client):
    match = client.post("/api/matches", json={}).json()
    response = client.post(f"/api/matches/{match['id']}/player-identities")
    assert response.status_code == 400
