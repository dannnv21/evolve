"""Regla común de franjas: ningún empleado puede tener dos citas solapadas.

La comprueba la API antes de escribir (para dar un 409 claro) y la vuelve a
garantizar el trigger trg_citas_no_solape de la base de datos.
"""
from datetime import date, datetime, time, timedelta
from typing import Optional

from flask import current_app

from ..db import get_db
from ..errors import Conflict, NotFound

RECEPTION_ROLES = ("admin", "jefe_taller")


def find_conflicts(employee_id: int, starts_at: datetime, ends_at: datetime,
                   exclude_event_id: Optional[int] = None) -> list[dict]:
    return get_db().fetch_all(
        """SELECT id AS cita_id, titulo, inicio, fin FROM citas
            WHERE empleado_id = %s AND estado <> 'cancelada' AND id <> %s
              AND inicio < %s AND fin > %s
            ORDER BY inicio""",
        (employee_id, exclude_event_id or 0, ends_at, starts_at))


def lock_employee(employee_id: int) -> dict:
    """Bloquea la fila del empleado hasta el final de la transacción para evitar reservas simultáneas."""
    employee = get_db().fetch_one(
        "SELECT id, disponibilidad, fecha_baja FROM empleados WHERE id = %s FOR UPDATE", (employee_id,))
    if employee is None or employee["fecha_baja"] is not None:
        raise NotFound("Empleado no encontrado")
    return employee


def ensure_slot_free(employee_id: int, starts_at: datetime, ends_at: datetime,
                     exclude_event_id: Optional[int] = None) -> None:
    lock_employee(employee_id)
    conflicts = find_conflicts(employee_id, starts_at, ends_at, exclude_event_id)
    if conflicts:
        first = conflicts[0]
        raise Conflict("Franja horaria ocupada para este empleado", "SLOT_TAKEN",
                       fields={"conflicto": {"cita_id": first["cita_id"], "titulo": first["titulo"],
                                             "inicio": first["inicio"], "fin": first["fin"]}})


def receptionist_ids() -> list[int]:
    rows = get_db().fetch_all(
        f"""SELECT e.id FROM empleados e
              JOIN usuarios u ON u.id = e.usuario_id JOIN roles r ON r.id = u.rol_id
             WHERE r.codigo IN ({", ".join(["%s"] * len(RECEPTION_ROLES))})
               AND e.fecha_baja IS NULL AND e.disponibilidad = 'disponible' AND u.activo = TRUE
             ORDER BY e.id""", RECEPTION_ROLES)
    return [row["id"] for row in rows]


def _opening_hours(day: date) -> tuple[datetime, datetime]:
    opens = time.fromisoformat(current_app.config["WORKSHOP_OPENS_AT"])
    closes = time.fromisoformat(current_app.config["WORKSHOP_CLOSES_AT"])
    return datetime.combine(day, opens), datetime.combine(day, closes)


def is_working_day(day: date) -> bool:
    return day.weekday() in current_app.config["WORKSHOP_WORKING_DAYS"]


def entry_slots(day: date, duration_minutes: int) -> list[dict]:
    """Franjas de entrada de un día; una franja está libre si algún recepcionista no tiene cita."""
    if not is_working_day(day):
        return []
    opens_at, closes_at = _opening_hours(day)
    staff_ids = receptionist_ids()
    busy_by_employee: dict[int, list[tuple[datetime, datetime]]] = {employee: [] for employee in staff_ids}
    if staff_ids:
        rows = get_db().fetch_all(
            f"""SELECT empleado_id, inicio, fin FROM citas
                 WHERE estado <> 'cancelada' AND inicio < %s AND fin > %s
                   AND empleado_id IN ({", ".join(["%s"] * len(staff_ids))})""",
            (closes_at, opens_at, *staff_ids))
        for row in rows:
            busy_by_employee[row["empleado_id"]].append((row["inicio"], row["fin"]))

    step = timedelta(minutes=duration_minutes)
    slots, slot_start = [], opens_at
    while slot_start + step <= closes_at:
        slot_end = slot_start + step
        free = any(all(not (busy_start < slot_end and busy_end > slot_start) for busy_start, busy_end in busy)
                   for busy in busy_by_employee.values())
        slots.append({"inicio": slot_start.strftime("%H:%M"), "fin": slot_end.strftime("%H:%M"), "libre": free})
        slot_start = slot_end
    return slots


def find_free_receptionist(starts_at: datetime, ends_at: datetime) -> Optional[int]:
    for employee_id in receptionist_ids():
        lock_employee(employee_id)
        if not find_conflicts(employee_id, starts_at, ends_at):
            return employee_id
    return None


def within_opening_hours(starts_at: datetime, ends_at: datetime) -> bool:
    if starts_at.date() != ends_at.date() or not is_working_day(starts_at.date()):
        return False
    opens_at, closes_at = _opening_hours(starts_at.date())
    return opens_at <= starts_at and ends_at <= closes_at
