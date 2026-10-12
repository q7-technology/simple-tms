"""Fleet care: the pre-start checklists, the checks drivers send, and what a
fleet manager records about each truck. Checks and records are kept as they
were sent; a truck's standing is worked out from them."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tms.api.deps import DB, Principal, require
from tms.api.routes.vehicles import VehicleType
from tms.models import ChecklistItem
from tms.services import checklists

router = APIRouter(tags=["fleet"])


# ---- checklists ----------------------------------------------------------------

class ChecklistLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80)
    critical: bool = False


class ChecklistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ChecklistLine] = Field(min_length=1, max_length=30)

    @field_validator("items")
    @classmethod
    def _once_each(cls, items: list[ChecklistLine]) -> list[ChecklistLine]:
        labels = [i.label.casefold() for i in items]
        if len(labels) != len(set(labels)):
            raise ValueError("each item once: two have the same label")
        return items


class ChecklistItemOut(BaseModel):
    id: int
    label: str
    critical: bool


def item_out(i: ChecklistItem) -> ChecklistItemOut:
    return ChecklistItemOut(id=i.id, label=i.label, critical=i.critical)


@router.get("/checklists", response_model=dict[str, list[ChecklistItemOut]])
def get_checklists(db: DB, who: Principal = require("fleet:read")):
    """Each type's checklist, in order: { "van": [...], "rigid": [...], "ute": [...] }."""
    return {t: [item_out(i) for i in checklists.live(db, t)] for t in checklists.TYPES}


@router.put("/checklists/{vehicle_type}", response_model=list[ChecklistItemOut])
def put_checklist(vehicle_type: VehicleType, body: ChecklistIn, db: DB,
                  who: Principal = require("fleet:write")):
    """The whole list for one type, in order. Lines that go are retired."""
    rows = checklists.replace(db, vehicle_type, [(i.label, i.critical) for i in body.items])
    db.commit()
    return [item_out(i) for i in rows]
