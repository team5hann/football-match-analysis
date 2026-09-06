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

    identities = group_tracks_into_identities(tracks)

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

    identities = group_tracks_into_identities(tracks)
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

    identities = group_tracks_into_identities(tracks)

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

    cluster, number, confidence = summarise_track(detections)

    assert cluster == 2
    assert number == 11
    assert confidence == round((0.9 + 0.8) / 2, 4)


def test_no_tracks_yields_no_identities():
    assert group_tracks_into_identities({}) == []


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
            1: [(0, 9, 0.9), (0, 9, 0.85)],
            2: [(0, 9, 0.8)],            # same colour + number as track 1 -> merge
            3: [(1, 4, 0.9)],           # distinct identified player
            4: [(1, None, None)],       # no number -> Unknown, separate
        }
    )

    response = client.post(f"/api/matches/{match_id}/player-identities")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["track_count"] == 4
    assert body["identified_count"] == 2  # {cluster0 #9}, {cluster1 #4}
    assert body["unknown_count"] == 1     # track 4

    merged = next(player for player in body["players"] if player["jersey_number"] == 9)
    assert sorted(merged["track_ids"]) == [1, 2]
    assert merged["is_unknown"] is False
    assert merged["detection_count"] == 3

    unknown = next(player for player in body["players"] if player["is_unknown"])
    assert unknown["track_ids"] == [4]
    assert unknown["label"] == "Unknown · Unknown #4"

    # GET returns the same stored result without recomputing.
    again = client.get(f"/api/matches/{match_id}/player-identities")
    assert again.status_code == 200
    assert again.json()["identified_count"] == 2
    assert again.json()["unknown_count"] == 1


def test_player_identities_endpoint_requires_analysis(client):
    match = client.post("/api/matches", json={}).json()
    response = client.post(f"/api/matches/{match['id']}/player-identities")
    assert response.status_code == 400
