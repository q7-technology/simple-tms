"""Where a truck stands: grounded by a failed pre-start, and when its rego
and service fall due. Worked out from the checks and records, never kept on
the truck itself, so the history is the truth.

Due means amber: 30 days or 1,000 km out. Overdue means red, and an overdue
truck can't take a run, any more than a grounded one (rule 3). Days are
judged for a given day: a run's own day when planning it, so a truck whose
rego runs out tomorrow can go today but not on tomorrow's run, whatever the
server's clock (UTC) says. Kilometres are whole numbers: an odometer shows
whole kilometres, and so does every interval."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tms.api.errors import Conflict
from tms.models import PrestartCheck, Vehicle, VehicleRecord

DUE_DAYS = 30
DUE_KM = 1_000
# what is known outweighs what isn't: a truck serviced by km alone is judged by them
WORST = {"unknown": -1, "ok": 0, "due": 1, "overdue": 2}


def add_months(d: date, months: int) -> date:
    """14 Jul plus 6 months is 14 Jan; 31 Aug plus 6 is the last day of February."""
    m = d.month - 1 + months
    year, month = d.year + m // 12, m % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def long_date(d: date) -> str:
    """3 Oct 2026"""
    return f"{d.day} {d.strftime('%b')} {d.year}"


def latest(db: Session, vehicle_id: int, kind: str) -> VehicleRecord | None:
    """The newest record of a kind: a newer one puts an older one right."""
    return db.execute(select(VehicleRecord).where(
        VehicleRecord.vehicle_id == vehicle_id, VehicleRecord.kind == kind)
        .order_by(VehicleRecord.id.desc()).limit(1)).scalar_one_or_none()


def odometer(db: Session, vehicle_id: int) -> int | None:
    """The truck's reading: the highest from a pre-start or a service since the
    last time a fleet manager put the reading right (or that reading, if
    higher). An odometer only goes up, so a service recorded late can't
    take it back."""
    put_right = latest(db, vehicle_id, "odometer")
    since = put_right.recorded_at if put_right else None
    checks = select(func.max(PrestartCheck.odometer_km)).where(
        PrestartCheck.vehicle_id == vehicle_id)
    services = select(func.max(VehicleRecord.odometer_km)).where(
        VehicleRecord.vehicle_id == vehicle_id, VehicleRecord.kind == "service")
    if since is not None:
        checks = checks.where(PrestartCheck.recorded_at > since)
        services = services.where(VehicleRecord.recorded_at > since)
    readings = [db.execute(checks).scalar(), db.execute(services).scalar(),
                put_right.odometer_km if put_right else None]
    known = [r for r in readings if r is not None]
    return max(known) if known else None


def grounding(db: Session, vehicle_id: int) -> PrestartCheck | None:
    """The latest check with a failed critical item that no fleet manager has
    marked fixed since. A `fixed` record clears its check and every one before."""
    fixed_up_to = db.execute(select(func.max(VehicleRecord.check_id)).where(
        VehicleRecord.vehicle_id == vehicle_id, VehicleRecord.kind == "fixed")).scalar() or 0
    return db.execute(select(PrestartCheck).where(
        PrestartCheck.vehicle_id == vehicle_id, PrestartCheck.result == "grounded",
        PrestartCheck.id > fixed_up_to).order_by(PrestartCheck.id.desc()).limit(1)
    ).scalar_one_or_none()


def last_check(db: Session, vehicle_id: int) -> PrestartCheck | None:
    return db.execute(select(PrestartCheck).where(PrestartCheck.vehicle_id == vehicle_id)
                      .order_by(PrestartCheck.id.desc()).limit(1)).scalar_one_or_none()


def failed_critical(check: PrestartCheck) -> list[str]:
    return [i.label for i in check.items if i.critical and not i.passed]


def _by_days(left: int | None) -> str:
    if left is None:
        return "unknown"
    return "overdue" if left < 0 else "due" if left <= DUE_DAYS else "ok"


def _by_km(left: int | None) -> str:
    if left is None:
        return "unknown"
    return "overdue" if left < 0 else "due" if left <= DUE_KM else "ok"


@dataclass
class Standing:
    """A truck as things stand, for a day."""

    vehicle: Vehicle
    day: date
    odometer_km: int | None
    grounded_by: PrestartCheck | None
    last_check: PrestartCheck | None
    rego_expires_on: date | None
    last_service: VehicleRecord | None

    @property
    def rego_days_left(self) -> int | None:
        return None if self.rego_expires_on is None else (self.rego_expires_on - self.day).days

    @property
    def rego_state(self) -> str:
        return _by_days(self.rego_days_left)

    @property
    def service_due_on(self) -> date | None:
        months = self.vehicle.service_every_months
        if not months or self.last_service is None:
            return None
        return add_months(self.last_service.done_on, months)

    @property
    def service_due_km(self) -> int | None:
        km = self.vehicle.service_every_km
        if not km or self.last_service is None or self.last_service.odometer_km is None:
            return None
        return self.last_service.odometer_km + km

    @property
    def service_days_left(self) -> int | None:
        due = self.service_due_on
        return None if due is None else (due - self.day).days

    @property
    def service_km_left(self) -> int | None:
        due = self.service_due_km
        return None if due is None or self.odometer_km is None else due - self.odometer_km

    @property
    def service_state(self) -> str:
        by_date, by_km = _by_days(self.service_days_left), _by_km(self.service_km_left)
        return max(by_date, by_km, key=WORST.__getitem__)

    @property
    def state(self) -> str:
        """grounded, overdue (red), due (amber) or ok."""
        if self.grounded_by is not None:
            return "grounded"
        worst = max(self.rego_state, self.service_state, key=WORST.__getitem__)
        return worst if worst in ("overdue", "due") else "ok"

    @property
    def blocked(self) -> Conflict | None:
        """Why it can't take a run on its day, or None. Grounded first, then
        rego, then service; each says what puts it right."""
        rego = self.vehicle.rego
        if self.grounded_by is not None:
            items = " and ".join(failed_critical(self.grounded_by)) or "a critical item"
            return Conflict("vehicle_grounded",
                            f"{rego} is grounded: {items} failed the pre-start. "
                            "A fleet manager has to mark it fixed first.")
        if self.rego_state == "overdue":
            return Conflict("vehicle_overdue",
                            f"{rego}'s rego runs out on {long_date(self.rego_expires_on)}. "
                            "Record the new expiry in Fleet first.")
        if _by_days(self.service_days_left) == "overdue":
            return Conflict("vehicle_overdue",
                            f"{rego}'s service was due by {long_date(self.service_due_on)}. "
                            "Record the service in Fleet first.")
        if _by_km(self.service_km_left) == "overdue":
            return Conflict("vehicle_overdue",
                            f"{rego}'s service was due at {self.service_due_km:,} km and it "
                            f"has done {self.odometer_km:,}. Record the service in Fleet first.")
        return None


def standing(db: Session, vehicle: Vehicle, day: date) -> Standing:
    rego = latest(db, vehicle.id, "rego")
    return Standing(vehicle=vehicle, day=day, odometer_km=odometer(db, vehicle.id),
                    grounded_by=grounding(db, vehicle.id), last_check=last_check(db, vehicle.id),
                    rego_expires_on=rego.expires_on if rego else None,
                    last_service=latest(db, vehicle.id, "service"))


def refuse_if_blocked(db: Session, vehicle: Vehicle, day: date) -> None:
    """Rule 3: a grounded or overdue truck does not go on a run."""
    why = standing(db, vehicle, day).blocked
    if why is not None:
        raise why
