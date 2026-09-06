from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MatchStatus
from app.schemas.team import TeamRead
from app.schemas.video import VideoRead


class MatchBase(BaseModel):
    name: str | None = None
    home_team_id: int | None = None
    away_team_id: int | None = None
    competition: str | None = None
    match_date: datetime | None = None
    home_score: int | None = None
    away_score: int | None = None
    home_team_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    away_team_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class MatchCreate(MatchBase):
    pass


class MatchUpdate(BaseModel):
    name: str | None = None
    home_team_id: int | None = None
    away_team_id: int | None = None
    competition: str | None = None
    match_date: datetime | None = None
    home_score: int | None = None
    away_score: int | None = None
    status: MatchStatus | None = None


class MatchRead(MatchBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: MatchStatus
    created_at: datetime
    updated_at: datetime


class MatchDetail(MatchRead):
    home_team: TeamRead | None = None
    away_team: TeamRead | None = None
    videos: list[VideoRead] = []
