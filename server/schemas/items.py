"""Item-related request/response schemas."""

from pydantic import BaseModel, Field


class ItemInput(BaseModel):
    """Item data for creation with all Homebox fields.

    In Homebox 0.26+, both fields map to ``parentId``.
    The selected item container in ``parent_id`` takes precedence over
    ``location_id`` and the batch location.
    """

    name: str
    quantity: int = 1
    description: str | None = None
    location_id: str | None = None
    tag_ids: list[str] | None = None
    parent_id: str | None = None
    # Advanced fields
    serial_number: str | None = None
    model_number: str | None = None
    manufacturer: str | None = None
    purchase_price: float | None = Field(default=None, ge=0)
    purchase_from: str | None = None
    notes: str | None = None
    insured: bool = False
    # Custom fields: map of display name → text value
    custom_fields: dict[str, str] | None = None


class BatchCreateRequest(BaseModel):
    """Batch item creation request."""

    items: list[ItemInput]
    location_id: str | None = None
