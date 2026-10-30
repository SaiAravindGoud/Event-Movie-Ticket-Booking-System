
from pydantic import BaseModel, ConfigDict, Field


class SeatBulkCreate(BaseModel):
    rows: list[str] = Field(min_length=1, max_length=100)
    seats_per_row: int = Field(gt=0, le=100)
    category: str = Field(default="Silver", min_length=1, max_length=30)


class SeatStatusUpdate(BaseModel):
    is_active: bool


class SeatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    screen_id: int
    row_label: str
    seat_number: str
    category: str
    is_active: bool


class SeatLayoutResponse(BaseModel):
    screen_id: int
    rows: dict[str, list[SeatResponse]]
