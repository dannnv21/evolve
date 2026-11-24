from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request

from ...constants import ACTIVE_WORK_STATES
from ...db import get_db, placeholders
from ...errors import BadRequest
from ...http import as_date, as_datetime, order_code
from ...security import staff_permission

bp = Blueprint("admin_dashboard", __name__, url_prefix="/admin/dashboard")

ACTIVE_ORDER_STATES = ("solicitado", "presupuestado", *ACTIVE_WORK_STATES, "listo")


def _month_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, 1)
    next_month = datetime(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return start, next_month


def _percent_change(current: int, previous: int) -> float:
    return round((current - previous) * 100 / previous, 1) if previous else 0.0


@bp.get("/summary")
@staff_permission("pedidos")
def summary():
    db = get_db()
    today = date.today()
    active = db.fetch_value(f"SELECT COUNT(*) FROM pedidos WHERE estado IN ({placeholders(len(ACTIVE_ORDER_STATES))})",
                            ACTIVE_ORDER_STATES)
    new_since_yesterday = db.fetch_value("SELECT COUNT(*) FROM pedidos WHERE enviado_en >= %s",
                                         (datetime.combine(today - timedelta(days=1), datetime.min.time()),))
    in_progress = db.fetch_value("SELECT COUNT(*) FROM pedidos WHERE estado = 'en_proceso'")
    in_workshop = db.fetch_value(
        "SELECT COUNT(*) FROM pedidos WHERE estado IN ('en_proceso', 'esperando_piezas', 'listo')")
    waiting_parts = db.fetch_value("SELECT COUNT(*) FROM pedidos WHERE estado = 'esperando_piezas'")
    critical_parts = db.fetch_value("SELECT COUNT(*) FROM piezas WHERE activo = TRUE AND stock <= stock_minimo")

    month_start, next_month = _month_bounds(today)
    previous_start, _ = _month_bounds(month_start.date() - timedelta(days=1))
    delivered = db.fetch_all("SELECT fecha_entrada, entrega_real FROM pedidos WHERE entrega_real >= %s AND entrega_real < %s",
                             (month_start, next_month))
    delivered_previous = db.fetch_value(
        "SELECT COUNT(*) FROM pedidos WHERE entrega_real >= %s AND entrega_real < %s", (previous_start, month_start))
    durations = [(as_datetime(row["entrega_real"]) - as_datetime(row["fecha_entrada"])).total_seconds() / 86400
                 for row in delivered if row["fecha_entrada"]]
    return jsonify({
        "pedidos_activos": {"valor": active, "nuevos_desde_ayer": new_since_yesterday},
        "en_taller": {"valor": in_workshop, "en_proceso": in_progress, "pendientes": in_workshop - in_progress},
        "esperando_piezas": {"valor": waiting_parts, "alertas": critical_parts},
        "completados_mes": {"valor": len(delivered), "variacion_pct": _percent_change(len(delivered), delivered_previous),
                            "dias_medios": round(sum(durations) / len(durations), 1) if durations else None},
    })


@bp.get("/agenda")
@staff_permission("calendario")
def agenda():
    try:
        day = as_date(request.args.get("date") or date.today().isoformat())
    except ValueError as error:
        raise BadRequest("Formato de fecha YYYY-MM-DD") from error
    start = datetime.combine(day, datetime.min.time())
    rows = get_db().fetch_all(
        """SELECT c.id, c.tipo, c.titulo, c.inicio, c.fin, c.pedido_id, c.empleado_id,
                  u.nombre AS empleado_nombre, u.apellidos AS empleado_apellidos
             FROM citas c
             LEFT JOIN empleados e ON e.id = c.empleado_id LEFT JOIN usuarios u ON u.id = e.usuario_id
            WHERE c.estado <> 'cancelada' AND c.inicio >= %s AND c.inicio < %s
            ORDER BY c.inicio""", (start, start + timedelta(days=1)))
    data = [{"id": row["id"], "tipo": row["tipo"], "titulo": row["titulo"],
             "inicio": as_datetime(row["inicio"]).strftime("%H:%M"), "fin": as_datetime(row["fin"]).strftime("%H:%M"),
             "empleado": f"{row['empleado_nombre']} {row['empleado_apellidos']}" if row["empleado_id"] else None,
             "pedido": order_code(row["pedido_id"]) if row["pedido_id"] else None} for row in rows]
    return jsonify({"fecha": day, "data": data})


@bp.get("/team-load")
@staff_permission("equipo")
def team_load():
    rows = get_db().fetch_all(
        f"""SELECT e.id AS empleado_id, u.nombre, u.apellidos, pu.nombre AS puesto, e.especialidad,
                   e.disponibilidad, e.capacidad_semanal, COUNT(p.id) AS pedidos_activos
              FROM empleados e
              JOIN usuarios u ON u.id = e.usuario_id
              JOIN puestos pu ON pu.id = e.puesto_id
              LEFT JOIN pedidos p ON p.responsable_id = e.id
                   AND p.estado IN ({placeholders(len(ACTIVE_WORK_STATES))})
             WHERE e.fecha_baja IS NULL
             GROUP BY e.id, u.nombre, u.apellidos, pu.nombre, e.especialidad, e.disponibilidad, e.capacidad_semanal
             ORDER BY pedidos_activos DESC, u.nombre""", ACTIVE_WORK_STATES)
    return jsonify({"data": [{
        "empleado_id": row["empleado_id"], "empleado": f"{row['nombre']} {row['apellidos']}", "puesto": row["puesto"],
        "especialidad": row["especialidad"], "disponibilidad": row["disponibilidad"],
        "pedidos_activos": row["pedidos_activos"],
        "carga_pct": round(100 * row["pedidos_activos"] / row["capacidad_semanal"])} for row in rows]})
