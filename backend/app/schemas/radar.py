from pydantic import BaseModel


class RadarScores(BaseModel):
    attack: float
    passing: float
    defence: float
    pressing: float
    transitions: float


class TeamRadar(BaseModel):
    team_role: str
    scores: RadarScores
    detail: dict


class MatchRadarRead(BaseModel):
    match_id: int
    home: TeamRadar
    away: TeamRadar
    notes: dict
