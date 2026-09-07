"""Five-axis team performance radar for a single match.

Every axis is a 0-10 score built from data the app already produces. Two of the
five - Pressing and Transitions - are ROUGH APPROXIMATIONS with no proper
football metric behind them (no PPDA, no event-level transition model); they are
labelled as such in the response ``notes`` and should be read as a rough shape,
not a number to quote.

Scaling: each raw quantity is multiplied by a hand-picked constant so that a
solid-but-not-exceptional match lands around 5-7, then clamped to [0, 10]. The
constants are guesses tuned on the sample clips, not calibrated on a dataset.
"""
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import PlayerMetric
from app.models.event import Event
from app.models.match import Match
from app.models.player_identity import MatchPlayer, TrackPlayerLink
from app.models.player_stats import MatchPlayerStats
from app.services.player_stats import PITCH_LENGTH_METERS, _load_match_inputs, compute_advanced_stats

# 0-10 scaling constants (see module docstring - these are tuned by eye and will
# still saturate to 10 for lop-sided matches).
ATTACK_SHOT_WEIGHT = 0.25
ATTACK_XG_WEIGHT = 1.2
ATTACK_FINAL_THIRD_PASS_WEIGHT = 0.04
ATTACK_DRIBBLE_WEIGHT = 0.4
# Pressing is normalised per minute so a longer clip doesn't auto-max it. The
# reference is a "high" forced-turnover rate in this (noisy) possession data.
PRESSING_TURNOVERS_PER_MIN_REF = 70.0
TRANSITIONS_SPEED_WEIGHT = 1.3       # median player m/s (capped) * this -> score
# The per-player average speed coming out of analysis is noisy (sparse sampling
# + homography jitter can produce tens of m/s), so cap each contribution to a
# plausible football average before taking the median.
TRANSITIONS_SPEED_CAP_MPS = 6.0
FINAL_THIRD_FRACTION = 1.0 / 3.0     # a pass "into the final third" ends past this line


def _clamp10(value: float) -> float:
    return round(max(0.0, min(10.0, value)), 1)


def _team_cluster_ids(match: Match, role: str) -> set[int]:
    return {a.cluster_id for a in match.team_cluster_assignments if a.role == role}


def compute_team_radar(match_id: int, db: Session) -> dict:
    match, video, records, pass_events, shot_events, cluster_roles = _load_match_inputs(match_id, db)
    minutes = max((video.duration_seconds or 0.0) / 60.0, 0.1)

    # One recompute gives us per-pass end positions + duel/possession detail.
    computed = compute_advanced_stats(
        records, pass_events, shot_events, video.width or 1, video.height or 1, cluster_roles
    )

    # track_id -> role, for attributing events/passes to a team.
    role_by_cluster = {a.cluster_id: a.role for a in match.team_cluster_assignments}
    links = db.scalars(select(TrackPlayerLink).where(TrackPlayerLink.match_id == match_id)).all()
    mp_by_track = {link.track_id: link.match_player_id for link in links}
    identity_cluster = {
        identity.id: identity.team_color_cluster
        for identity in db.scalars(select(MatchPlayer).where(MatchPlayer.match_id == match_id)).all()
    }

    def track_role(track_id: int | None) -> str:
        cluster = identity_cluster.get(mp_by_track.get(track_id))
        return role_by_cluster.get(cluster, "unknown")

    # final-third pass counts per role (either 18-yd third counts as "attacking";
    # we don't know each team's attack direction reliably, so both ends count).
    final_third_passes = {"home": 0, "away": 0}
    low_line = PITCH_LENGTH_METERS * FINAL_THIRD_FRACTION
    high_line = PITCH_LENGTH_METERS * (1 - FINAL_THIRD_FRACTION)
    for attempt in computed.pass_attempts:
        if not attempt["completed"]:
            continue
        role = track_role(attempt["sender_track_id"])
        if role in final_third_passes and (attempt["end_x"] <= low_line or attempt["end_x"] >= high_line):
            final_third_passes[role] += 1

    # forced turnovers = possession_loss events whose new owner is this team.
    forced_turnovers = {"home": 0, "away": 0}
    for event in db.scalars(
        select(Event).where(Event.match_id == match_id, Event.event_type == "possession_loss")
    ).all():
        role = track_role(event.track_id)
        if role in forced_turnovers:
            forced_turnovers[role] += 1

    teams = {}
    notes = {
        "attack": (
            "shots, total xG, completed passes into a final third, and successful "
            "dribbles, weighted and clamped to 0-10."
        ),
        "passing": "completed / attempted passes, x10.",
        "defence": "duels won / duels contested, x10 (duels are a rough heuristic).",
        "pressing": (
            "ROUGH APPROXIMATION - not PPDA. Opponent possession_loss events won by "
            "this team (turnovers forced), per minute, scaled to 0-10. Possession "
            "here flickers a lot, so this has low discriminating power."
        ),
        "transitions": (
            "ROUGH APPROXIMATION - median of this team's players' average speed "
            f"(m/s, capped at {TRANSITIONS_SPEED_CAP_MPS}), scaled to 0-10. The "
            "underlying per-player speeds are noisy (sparse sampling), so this axis "
            "is close to a constant - do not read into small differences."
        ),
    }

    for role in ("home", "away"):
        cluster_ids = _team_cluster_ids(match, role)
        identities = db.scalars(
            select(MatchPlayer).where(
                MatchPlayer.match_id == match_id, MatchPlayer.team_color_cluster.in_(cluster_ids or {-1})
            )
        ).all()
        identity_ids = {identity.id for identity in identities}
        stats = db.scalars(
            select(MatchPlayerStats).where(MatchPlayerStats.match_player_id.in_(identity_ids or {-1}))
        ).all()

        shots = sum(s.shots for s in stats)
        xg = sum(s.xg for s in stats)
        dribbles_ok = sum(s.dribbles_completed for s in stats)
        passes_total = sum(s.passes_total for s in stats)
        passes_completed = sum(s.passes_completed for s in stats)
        duels_total = sum(s.duels_total for s in stats)
        duels_won = sum(s.duels_won for s in stats)

        speeds = [
            min(metric.average_speed_mps, TRANSITIONS_SPEED_CAP_MPS)
            for metric in db.scalars(
                select(PlayerMetric).where(PlayerMetric.match_player_id.in_(identity_ids or {-1}))
            ).all()
            if metric.average_speed_mps
        ]
        mean_speed = median(speeds) if speeds else 0.0

        attack = _clamp10(
            shots * ATTACK_SHOT_WEIGHT
            + xg * ATTACK_XG_WEIGHT
            + final_third_passes[role] * ATTACK_FINAL_THIRD_PASS_WEIGHT
            + dribbles_ok * ATTACK_DRIBBLE_WEIGHT
        )
        passing = _clamp10((passes_completed / passes_total) * 10 if passes_total else 0.0)
        defence = _clamp10((duels_won / duels_total) * 10 if duels_total else 0.0)
        pressing = _clamp10(
            (forced_turnovers[role] / minutes) / PRESSING_TURNOVERS_PER_MIN_REF * 10
        )
        transitions = _clamp10(mean_speed * TRANSITIONS_SPEED_WEIGHT)

        teams[role] = {
            "team_role": role,
            "scores": {
                "attack": attack,
                "passing": passing,
                "defence": defence,
                "pressing": pressing,
                "transitions": transitions,
            },
            "detail": {
                "shots": shots,
                "xg": round(xg, 4),
                "final_third_passes": final_third_passes[role],
                "dribbles_completed": dribbles_ok,
                "passes_completed": passes_completed,
                "passes_total": passes_total,
                "duels_won": duels_won,
                "duels_total": duels_total,
                "forced_turnovers": forced_turnovers[role],
                "mean_player_speed_mps": round(mean_speed, 2),
            },
        }

    return {"match_id": match_id, "home": teams["home"], "away": teams["away"], "notes": notes}
