from app.services.tactical import calculate_tactical


def test_tactical_analysis_returns_formation_metrics_and_players():
    records = []
    positions = [(20, 10), (40, 10), (60, 10), (20, 50), (40, 50), (60, 50), (20, 90), (40, 90), (60, 90)]
    for timestamp in (0, 1):
        for track_id, (x, y) in enumerate(positions, start=1):
            records.append(
                {
                    "class": "player",
                    "track_id": track_id,
                    "timestamp": timestamp,
                    "cluster": 0,
                    "jersey_number": 10 if track_id == 1 else None,
                    "box": {"x": x, "y": y, "width": 10, "height": 10},
                }
            )

    result = calculate_tactical(records, "home", 100, 100, {0: "home"})

    assert result.formation == "3-3-3"
    assert result.width > 0
    assert result.depth > 0
    assert result.compactness > 0
    assert len(result.players) == 9
    assert "homography" in result.coordinate_note and "fall back" in result.coordinate_note