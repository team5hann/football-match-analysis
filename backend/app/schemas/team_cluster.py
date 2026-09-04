from pydantic import BaseModel, Field


class TeamClusterAssignmentUpdate(BaseModel):
    cluster_id: int = Field(ge=0, le=2)
    role: str = Field(pattern="^(home|away|referee)$")
    team_id: int | None = None


class TeamClusterAssignmentRead(TeamClusterAssignmentUpdate):
    id: int
    detections_count: int = 0