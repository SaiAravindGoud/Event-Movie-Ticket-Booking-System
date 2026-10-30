
from datetime import date, time, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SeatCategory = Literal["Silver", "Gold", "Recliner"]


class SeatCategoryPriceCreate(BaseModel):
    category: SeatCategory
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


class SeatCategoryPriceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: SeatCategory
    price: Decimal


class ShowCreate(BaseModel):
    screen_id: int = Field(gt=0)
    show_date: date
    start_time: time
    prices: list[SeatCategoryPriceCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_prices(self):
        categories = [item.category for item in self.prices]
        if len(categories) != len(set(categories)):
            raise ValueError("Each seat category can have only one price")
        return self


class ShowUpdate(BaseModel):
    show_date: date | None = None
    start_time: time | None = None
    screen_id: int | None = Field(default=None, gt=0)
    prices: list[SeatCategoryPriceCreate] | None = Field(
        default=None, min_length=1
    )

    @model_validator(mode="after")
    def validate_prices(self):
        if self.prices is not None:
            categories = [item.category for item in self.prices]
            if len(categories) != len(set(categories)):
                raise ValueError("Each seat category can have only one price")
        return self


class ShowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    screen_id: int
    start_time: datetime
    end_time: datetime
    base_price: Decimal
    silver_price: Decimal
    gold_price: Decimal
    recliner_price: Decimal
    booking_closes_at: datetime | None
    status: str
    prices: list[SeatCategoryPriceResponse] = []
