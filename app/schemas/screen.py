
from pydantic import BaseModel, ConfigDict, Field


class ScreenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    total_rows: int = Field(gt=0, le=100)
    seats_per_row: int = Field(gt=0, le=100)


class ScreenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    venue_id: int
    name: str
    total_rows: int
    seats_per_row: int
    total_seats: int
