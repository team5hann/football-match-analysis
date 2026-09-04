from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.database import SessionLocal, get_db
from app.models.match import Match
from app.models.detection import Detection
from app.models.team_cluster import TeamClusterAssignment
from app.models.video import Video
from app.models.analysis import MatchAnalysisSummary, PlayerMetric
from app.models.event import Event
from app.models.enums import MatchStatus
from app.schemas.analysis import AnalysisRead, PlayerMetricRead
from app.services.analysis import run_analysis
from app.schemas.match import MatchCreate, MatchDetail, MatchRead, MatchUpdate
from app.schemas.team_cluster import TeamClusterAssignmentRead, TeamClusterAssignmentUpdate
from app.schemas.heatmap import HeatmapRead
from app.services.heatmap import build_heatmap
from app.schemas.passing_network import PassingNetworkRead
from app.services.passing_network import build_passing_network
from app.schemas.tactical import TacticalRead, TacticalPlayerRead
from app.services.tactical import calculate_tactical
from app.schemas.shots import ShotRead, ShotsRead
from app.services.shots import detect_shots
from app.schemas.player_option import PlayerOptionRead
from app.services.player_options import build_player_options

router = APIRouter(prefix="/api/matches", tags=["matches"])


def _detail_query():
    return select(Match).options(
        joinedload(Match.home_team),
        joinedload(Match.away_team),
        joinedload(Match.videos),
    )


@router.get("", response_model=list[MatchRead])
def list_matches(db: Session = Depends(get_db)) -> list[Match]:
    return db.execute(select(Match).order_by(Match.created_at.desc())).scalars().all()


@router.post("", response_model=MatchDetail, status_code=201)
def create_match(payload: MatchCreate, db: Session = Depends(get_db)) -> Match:
    match = Match(**payload.model_dump())
    db.add(match)
    db.commit()
    match = db.execute(_detail_query().where(Match.id == match.id)).unique().scalar_one()
    return match


@router.get("/{match_id}", response_model=MatchDetail)
def get_match(match_id: int, db: Session = Depends(get_db)) -> Match:
    match = db.execute(_detail_query().where(Match.id == match_id)).unique().scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


@router.put("/{match_id}", response_model=MatchDetail)
def update_match(match_id: int, payload: MatchUpdate, db: Session = Depends(get_db)) -> Match:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(match, field, value)
    db.commit()
    match = db.execute(_detail_query().where(Match.id == match_id)).unique().scalar_one()
    return match


@router.delete("/{match_id}", status_code=204)
def delete_match(match_id: int, db: Session = Depends(get_db)) -> None:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    db.delete(match)
    db.commit()


@router.get("/{match_id}/team-clusters", response_model=list[TeamClusterAssignmentRead])
def list_team_clusters(match_id: int, db: Session = Depends(get_db)) -> list[TeamClusterAssignmentRead]:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    assignments = db.scalars(
        select(TeamClusterAssignment).where(TeamClusterAssignment.match_id == match_id).order_by(TeamClusterAssignment.cluster_id)
    ).all()
    result = []
    for assignment in assignments:
        count = db.scalar(
            select(func.count(Detection.id))
            .join(Video, Detection.video_id == Video.id)
            .where(
                Video.match_id == match_id,
                Detection.class_name == "player",
                Detection.team_color_cluster == assignment.cluster_id,
            )
        )
        result.append(
            TeamClusterAssignmentRead(
                id=assignment.id,
                cluster_id=assignment.cluster_id,
                role=assignment.role,
                team_id=assignment.team_id,
                detections_count=count or 0,
            )
        )
    return result


@router.put("/{match_id}/team-clusters", response_model=list[TeamClusterAssignmentRead])
def save_team_clusters(
    match_id: int, payload: list[TeamClusterAssignmentUpdate], db: Session = Depends(get_db)
) -> list[TeamClusterAssignmentRead]:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    existing = {item.cluster_id: item for item in db.scalars(
        select(TeamClusterAssignment).where(TeamClusterAssignment.match_id == match_id)
    ).all()}
    for item in payload:
        assignment = existing.get(item.cluster_id)
        if assignment is None:
            assignment = TeamClusterAssignment(match_id=match_id, cluster_id=item.cluster_id)
            db.add(assignment)
        assignment.role = item.role
        assignment.team_id = item.team_id
    db.commit()
    return list_team_clusters(match_id, db)


@router.post("/{match_id}/analysis", response_model=AnalysisRead, status_code=202)
def start_analysis(match_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> AnalysisRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if not db.scalar(select(Detection.id).join(Video, Detection.video_id == Video.id).where(Video.match_id == match_id)):
        raise HTTPException(status_code=400, detail="Run player detection before analysis")
    match.status = MatchStatus.PROCESSING
    db.commit()
    background_tasks.add_task(run_analysis, match_id)
    return AnalysisRead(status="processing", home_possession_pct=0, away_possession_pct=0, players=[], events=[])


@router.get("/{match_id}/analysis", response_model=AnalysisRead)
def get_analysis(match_id: int, db: Session = Depends(get_db)) -> AnalysisRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    summary = db.scalar(select(MatchAnalysisSummary).where(MatchAnalysisSummary.match_id == match_id))
    metrics = db.scalars(select(PlayerMetric).where(PlayerMetric.match_id == match_id).order_by(PlayerMetric.track_id)).all()
    events = db.scalars(select(Event).where(Event.match_id == match_id, Event.event_type.in_(["pass", "possession_loss"])).order_by(Event.timestamp_seconds)).all()
    return AnalysisRead(
        status="analyzed" if summary else match.status.value if hasattr(match.status, "value") else match.status,
        home_possession_pct=summary.home_possession_pct if summary else 0,
        away_possession_pct=summary.away_possession_pct if summary else 0,
        players=[PlayerMetricRead.model_validate(metric, from_attributes=True) for metric in metrics],
        events=[
            {"event_type": event.event_type, "timestamp_seconds": event.timestamp_seconds, "track_id": event.track_id, "description": event.description}
            for event in events
        ],
    )


@router.get("/{match_id}/heatmap", response_model=HeatmapRead)
def get_heatmap(
    match_id: int,
    mode: str = Query("team", pattern="^(team|player)$"),
    track_id: int | None = Query(None, ge=1),
    team_color_cluster: int | None = Query(None, ge=0, le=2),
    grid_width: int = Query(20, ge=1, le=100),
    grid_height: int = Query(12, ge=1, le=100),
    db: Session = Depends(get_db),
) -> HeatmapRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if mode == "player" and track_id is None:
        raise HTTPException(status_code=400, detail="track_id is required for player heatmaps")
    if mode == "team" and team_color_cluster is None:
        raise HTTPException(status_code=400, detail="team_color_cluster is required for team heatmaps")

    query = (
        select(Detection, Video.width, Video.height)
        .join(Video, Detection.video_id == Video.id)
        .where(Video.match_id == match_id, Detection.class_name == "player")
    )
    if mode == "player":
        query = query.where(Detection.track_id == track_id)
    else:
        query = query.where(Detection.team_color_cluster == team_color_cluster)
    rows = db.execute(query).all()
    detections = [
        {"bounding_box": detection.bounding_box}
        for detection, _width, _height in rows
    ]
    width = next((width for _detection, width, _height in rows if width), 1)
    height = next((height for _detection, _width, height in rows if height), 1)
    grid = build_heatmap(detections, width, height, grid_width, grid_height)
    return HeatmapRead(
        mode=mode,
        track_id=track_id,
        team_color_cluster=team_color_cluster,
        grid_width=grid_width,
        grid_height=grid_height,
        grid=grid,
        total_observations=sum(sum(row) for row in grid),
        coordinate_note="Approximate camera coordinates from bounding-box centers; not calibrated to the real pitch.",
    )


@router.get("/{match_id}/players", response_model=list[PlayerOptionRead])
def list_detected_players(match_id: int, db: Session = Depends(get_db)) -> list[PlayerOptionRead]:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    detections = db.execute(
        select(Detection, Video.id)
        .join(Video, Detection.video_id == Video.id)
        .where(Video.match_id == match_id, Detection.class_name == "player", Detection.track_id.is_not(None))
    ).all()
    cluster_roles = {item.cluster_id: item.role for item in match.team_cluster_assignments}
    records = [
        {
            "class": detection.class_name,
            "track_id": detection.track_id,
            "team_color_cluster": detection.team_color_cluster,
            "jersey_number": detection.jersey_number,
        }
        for detection, _video_id in detections
    ]
    return [PlayerOptionRead.model_validate(option) for option in build_player_options(records, cluster_roles)]


@router.get("/{match_id}/passing-network", response_model=PassingNetworkRead)
def get_passing_network(match_id: int, db: Session = Depends(get_db)) -> PassingNetworkRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    video = db.scalar(select(Video).where(Video.match_id == match_id).order_by(Video.id))
    if video is None:
        raise HTTPException(status_code=400, detail="No video found")
    detections = db.scalars(
        select(Detection).where(Detection.video_id == video.id).order_by(Detection.frame_timestamp, Detection.id)
    ).all()
    events = db.scalars(
        select(Event).where(Event.match_id == match_id, Event.event_type == "pass").order_by(Event.timestamp_seconds, Event.id)
    ).all()
    cluster_roles = {item.cluster_id: item.role for item in match.team_cluster_assignments}
    records = [
        {"class": item.class_name, "track_id": item.track_id, "timestamp": item.frame_timestamp, "box": item.bounding_box, "cluster": item.team_color_cluster, "jersey_number": item.jersey_number}
        for item in detections
    ]
    event_records = [{"track_id": event.track_id, "timestamp": event.timestamp_seconds} for event in events]
    result = build_passing_network(records, event_records, video.width or 1, video.height or 1, cluster_roles)
    return PassingNetworkRead(
        home=result["home"],
        away=result["away"],
        coordinate_note="Approximate camera coordinates from detection box centers; not calibrated to the real pitch.",
    )


@router.get("/{match_id}/tactical", response_model=TacticalRead)
def get_tactical_analysis(
    match_id: int,
    team: str = Query("home", pattern="^(home|away)$"),
    db: Session = Depends(get_db),
) -> TacticalRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    video = db.scalar(select(Video).where(Video.match_id == match_id).order_by(Video.id))
    if video is None:
        raise HTTPException(status_code=400, detail="No video found")
    detections = db.scalars(
        select(Detection).where(Detection.video_id == video.id, Detection.class_name == "player").order_by(Detection.frame_timestamp, Detection.id)
    ).all()
    cluster_roles = {item.cluster_id: item.role for item in match.team_cluster_assignments}
    records = [
        {"class": item.class_name, "track_id": item.track_id, "timestamp": item.frame_timestamp, "box": item.bounding_box, "cluster": item.team_color_cluster, "jersey_number": item.jersey_number}
        for item in detections
        if item.track_id is not None
    ]
    result = calculate_tactical(records, team, video.width or 1, video.height or 1, cluster_roles)
    return TacticalRead(
        team_role=result.team_role,
        formation=result.formation,
        width=result.width,
        depth=result.depth,
        compactness=result.compactness,
        players=[TacticalPlayerRead.model_validate(player) for player in result.players],
        coordinate_note=result.coordinate_note,
    )


@router.post("/{match_id}/shots", response_model=ShotsRead, status_code=202)
def start_shot_detection(match_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> ShotsRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if not db.scalar(select(Detection.id).join(Video, Detection.video_id == Video.id).where(Video.match_id == match_id, Detection.class_name == "ball")):
        raise HTTPException(status_code=400, detail="No ball detections found")
    match.status = MatchStatus.PROCESSING
    db.commit()
    background_tasks.add_task(run_shot_detection, match_id)
    return ShotsRead(status="processing", home_xg=0, away_xg=0, shots=[], note="Shot detection is a heuristic based on sparse ball detections.")


def run_shot_detection(match_id: int) -> None:
    db = SessionLocal()
    match = db.get(Match, match_id)
    if match is None:
        db.close()
        return
    try:
        video = db.scalar(select(Video).where(Video.match_id == match_id).order_by(Video.id))
        if video is None:
            raise ValueError("No video found")
        detections = db.scalars(select(Detection).where(Detection.video_id == video.id).order_by(Detection.frame_timestamp, Detection.id)).all()
        cluster_roles = {item.cluster_id: item.role for item in match.team_cluster_assignments}
        records = [
            {"class": item.class_name, "track_id": item.track_id, "timestamp": item.frame_timestamp, "box": item.bounding_box, "cluster": item.team_color_cluster}
            for item in detections
        ]
        shots = detect_shots(
            [record for record in records if record["class"] == "ball"],
            [record for record in records if record["class"] == "player"],
            video.width or 1,
            video.height or 1,
            cluster_roles,
        )
        db.execute(delete(Event).where(Event.match_id == match_id, Event.event_type == "shot"))
        for shot in shots:
            db.add(Event(
                match_id=match_id,
                video_id=video.id,
                track_id=shot.track_id,
                event_type="shot",
                timestamp_seconds=shot.timestamp,
                position_x=shot.position_x,
                position_y=shot.position_y,
                xg=shot.xg,
                confidence=shot.xg,
                description=shot.description,
                manually_verified=False,
            ))
        match.status = MatchStatus.ANALYZED
        db.commit()
    except Exception:
        db.rollback()
        match.status = MatchStatus.FAILED
        db.commit()
    finally:
        db.close()


@router.get("/{match_id}/shots", response_model=ShotsRead)
def get_shots(match_id: int, db: Session = Depends(get_db)) -> ShotsRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    shots = db.scalars(select(Event).where(Event.match_id == match_id, Event.event_type == "shot").order_by(Event.timestamp_seconds, Event.id)).all()
    result = [
        ShotRead(
            id=shot.id,
            timestamp_seconds=shot.timestamp_seconds,
            track_id=shot.track_id,
            team_role="unknown",
            xg=shot.xg or 0,
            position_x=shot.position_x,
            position_y=shot.position_y,
            description=shot.description,
        )
        for shot in shots
    ]
    if shots:
        video = db.scalar(select(Video).where(Video.match_id == match_id).order_by(Video.id))
        if video:
            detections = db.scalars(select(Detection).where(Detection.video_id == video.id, Detection.class_name == "player")).all()
            cluster_roles = {item.cluster_id: item.role for item in match.team_cluster_assignments}
            role_by_track = {}
            for detection in detections:
                if detection.track_id is not None:
                    role_by_track.setdefault(detection.track_id, []).append(cluster_roles.get(detection.team_color_cluster, "unknown"))
            for item in result:
                roles = role_by_track.get(item.track_id, ["unknown"])
                item.team_role = max(set(roles), key=roles.count)
    home_xg = round(sum(item.xg for item in result if item.team_role == "home"), 4)
    away_xg = round(sum(item.xg for item in result if item.team_role == "away"), 4)
    return ShotsRead(status="analyzed" if shots else (match.status.value if hasattr(match.status, "value") else match.status), home_xg=home_xg, away_xg=away_xg, shots=result, note="Shot detection is a rough heuristic from sparse ball detections; many shots may be missed.")
