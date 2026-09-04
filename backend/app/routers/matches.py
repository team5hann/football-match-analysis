from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
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
