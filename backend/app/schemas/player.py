from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import PlayerPosition


class PlayerBase(BaseModel):
    full_name: str
    team_id: int | None = None
    jersey_number: int | None = None
    position: PlayerPosition = PlayerPosition.UNKNOWN
    date_of_birth: datetime | None = None


class PlayerCreate(PlayerBase):
    pass


class PlayerUpdate(BaseModel):
    full_name: str | None = None
    team_id: int | None = None
    jersey_number: int | None = None
    position: PlayerPosition | None = None
    date_of_birth: datetime | None = None


class PlayerRead(PlayerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
