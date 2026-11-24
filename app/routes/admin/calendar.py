from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify

from ...db import get_db
from ...errors import BadRequest, NotFound
from ...http import as_datetime, no_content, order_code, parse_body, parse_query
from ...schemas.workshop import CalendarQuery, EventIn, EventUpdateIn, SlotCheckQuery, changed_fields
from ...security import current_user, staff_permission
from ...services import scheduling
from ...services.orders import add_history

bp = Blueprint("admin_calendar", __name__, url_prefix="/admin/calendar")

EVENT_SELECT = """
    SELECT c.id, c.tipo, c.titulo, c.descripcion, c.pedido_id, c.empleado_id, c.inicio, c.fin, c.estado,
           u.nombre AS empleado_nombre, u.apellidos AS empleado_apellidos,
           v.marca, v.modelo, v.matricula, cl.nombre AS cliente_nombre, cl.apellidos AS cliente_apellidos
      FROM citas c
      LEFT JOIN empleados e ON e.id = c.empleado_id
      LEFT JOIN usuarios u ON u.id = e.usuario_id
      LEFT JOIN pedidos p ON p.id = c.pedido_id
      LEFT JOIN vehiculos v ON v.id = p.vehiculo_id
      LEFT JOIN usuarios cl ON cl.id = p.cliente_id
"""


def serialize_event(row: dict) -> dict:
    return {"id": row["id"], "tipo": row["tipo"], "titulo": row["titulo"], "descripcion": row["descripcion"],
            "pedido_id": row["pedido_id"], "pedido": order_code(row["pedido_id"]) if row["pedido_id"] else None,
            "empleado_id": row["empleado_id"],
            "empleado": f"{row['empleado_nombre']} {row['empleado_apellidos']}" if row["empleado_id"] else None,
            "vehiculo": f"{row['marca']} {row['modelo']}" if row["marca"] else None,
            "cliente": f"{row['cliente_nombre']} {row['cliente_apellidos']}" if row["cliente_nombre"] else None,
            "inicio": row["inicio"], "fin": row["fin"], "estado": row["estado"]}


def _event(event_id: int) -> dict:
    row = get_db().fetch_one(f"{EVENT_SELECT} WHERE c.id = %s", (event_id,))
    if row is None:
        raise NotFound("Evento no encontrado")
    return row


def _validate_links(order_id, employee_id) -> None:
    db = get_db()
    if order_id and not db.fetch_value("SELECT id FROM pedidos WHERE id = %s AND estado <> 'borrador'", (order_id,)):
        raise NotFound("Pedido no encontrado")
    if employee_id and not db.fetch_value("SELECT id FROM empleados WHERE id = %s AND fecha_baja IS NULL", (employee_id,)):
        raise NotFound("Empleado no encontrado")


@bp.get("")
@staff_permission("calendario")
def list_events():
    query = parse_query(CalendarQuery)
    week_start = date.today() - timedelta(days=date.today().weekday())
    start_day = query.from_ or week_start
    end_day = query.to or start_day + timedelta(days=6)
    conditions = ["c.estado <> 'cancelada'", "c.inicio >= %s", "c.inicio < %s"]
    params: list = [datetime.combine(start_day, datetime.min.time()),
                    datetime.combine(end_day + timedelta(days=1), datetime.min.time())]
    if query.empleado_id:
        conditions.append("c.empleado_id = %s")
        params.append(query.empleado_id)
    if query.cliente:
        pattern = f"%{query.cliente.lower()}%"
        conditions.append("(LOWER(cl.nombre) LIKE %s OR LOWER(cl.apellidos) LIKE %s OR LOWER(v.matricula) LIKE %s)")
        params += [pattern] * 3
    rows = get_db().fetch_all(f"{EVENT_SELECT} WHERE {' AND '.join(conditions)} ORDER BY c.inicio", params)
    events = [serialize_event(row) for row in rows]
    return jsonify({"desde": start_day, "hasta": end_day,
                    "trabajos_planificados": sum(1 for event in events if event["tipo"] == "trabajo"),
                    "data": events})


@bp.get("/check")
@staff_permission("calendario")
def check_slot():
    query = parse_query(SlotCheckQuery)
    conflicts = scheduling.find_conflicts(query.empleado_id, query.inicio, query.fin)
    return jsonify({"libre": not conflicts, "conflictos": [
        {**conflict, "inicio": as_datetime(conflict["inicio"]).strftime("%H:%M"),
         "fin": as_datetime(conflict["fin"]).strftime("%H:%M")} for conflict in conflicts]})


@bp.post("/events")
@staff_permission("calendario", write=True)
def create_event():
    data = parse_body(EventIn)
    db = get_db()
    with db.transaction():
        _validate_links(data.pedido_id, data.empleado_id)
        if data.empleado_id:
            scheduling.ensure_slot_free(data.empleado_id, data.inicio, data.fin)
        event_id = db.execute(
            """INSERT INTO citas (tipo, pedido_id, empleado_id, titulo, descripcion, inicio, fin, creado_por)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (data.tipo, data.pedido_id, data.empleado_id, data.titulo, data.descripcion, data.inicio, data.fin,
             current_user()["id"]))
        if data.pedido_id and data.tipo in ("entrada", "trabajo"):
            order_state = db.fetch_value("SELECT estado FROM pedidos WHERE id = %s", (data.pedido_id,))
            if order_state == "aceptado":
                db.execute("UPDATE pedidos SET estado = 'programado' WHERE id = %s", (data.pedido_id,))
                add_history(data.pedido_id, "aceptado", "programado", f"Programado para el {data.inicio:%d/%m %H:%M}")
    return jsonify(serialize_event(_event(event_id))), 201


@bp.patch("/events/<int:event_id>")
@staff_permission("calendario", write=True)
def update_event(event_id: int):
    changes = changed_fields(parse_body(EventUpdateIn))
    db = get_db()
    with db.transaction():
        event = _event(event_id)
        starts_at = as_datetime(changes.get("inicio", event["inicio"]))
        ends_at = as_datetime(changes.get("fin", event["fin"]))
        if ends_at <= starts_at:
            raise BadRequest("La hora de fin debe ser posterior a la de inicio")
        employee_id = changes.get("empleado_id", event["empleado_id"])
        _validate_links(None, employee_id)
        if employee_id and changes.get("estado", event["estado"]) != "cancelada":
            scheduling.ensure_slot_free(employee_id, starts_at, ends_at, exclude_event_id=event_id)
        if changes:
            assignments = ", ".join(f"{column} = %s" for column in changes)
            db.execute(f"UPDATE citas SET {assignments} WHERE id = %s", (*changes.values(), event_id))
    return jsonify(serialize_event(_event(event_id)))


@bp.delete("/events/<int:event_id>")
@staff_permission("calendario", write=True)
def cancel_event(event_id: int):
    _event(event_id)
    db = get_db()
    with db.transaction():
        db.execute("UPDATE citas SET estado = 'cancelada' WHERE id = %s", (event_id,))
    return no_content()
