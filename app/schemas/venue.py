
from pydantic import BaseModel, ConfigDict, Field


class VenueCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    city: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1)
    is_active: bool = True


class VenueUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    address: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None


class VenueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    city: str
    address: str
    is_active: bool
