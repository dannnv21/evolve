from datetime import date

from flask import Blueprint, jsonify, request

from ...constants import ACTIVE_WORK_STATES
from ...db import get_db, placeholders
from ...errors import BadRequest, Conflict, NotFound
from ...http import no_content, now, order_code, parse_body
from ...schemas.workshop import AvailabilityIn, EmployeeCreateIn, EmployeeUpdateIn, changed_fields
from ...security import hash_password, staff_permission
from ...services.accounts import ensure_unique_identity, generate_username, role_id

bp = Blueprint("admin_employees", __name__, url_prefix="/admin/employees")

EMPLOYEE_SELECT = f"""
    SELECT e.id, e.usuario_id, u.nombre, u.apellidos, u.email, r.codigo AS rol, e.puesto_id, pu.nombre AS puesto,
           e.especialidad, e.disponibilidad, e.ausente_hasta, e.capacidad_semanal, e.fecha_alta,
           (SELECT COUNT(*) FROM pedidos p WHERE p.responsable_id = e.id
               AND p.estado IN ({", ".join(f"'{state}'" for state in ACTIVE_WORK_STATES)})) AS pedidos_activos
      FROM empleados e
      JOIN usuarios u ON u.id = e.usuario_id
      JOIN roles r ON r.id = u.rol_id
      JOIN puestos pu ON pu.id = e.puesto_id
"""


def serialize_employee(row: dict) -> dict:
    return {"id": row["id"], "usuario_id": row["usuario_id"], "nombre": f"{row['nombre']} {row['apellidos']}",
            "email": row["email"], "rol": row["rol"], "puesto_id": row["puesto_id"], "puesto": row["puesto"],
            "especialidad": row["especialidad"], "disponibilidad": row["disponibilidad"],
            "ausente_hasta": row["ausente_hasta"], "capacidad_semanal": row["capacidad_semanal"],
            "fecha_alta": row["fecha_alta"], "pedidos_activos": row["pedidos_activos"],
            "carga_pct": round(100 * row["pedidos_activos"] / row["capacidad_semanal"])}


def _employee(employee_id: int) -> dict:
    row = get_db().fetch_one(f"{EMPLOYEE_SELECT} WHERE e.id = %s AND e.fecha_baja IS NULL", (employee_id,))
    if row is None:
        raise NotFound("Empleado no encontrado")
    return row


def _ensure_position(position_id: int) -> None:
    if not get_db().fetch_value("SELECT id FROM puestos WHERE id = %s AND activo = TRUE", (position_id,)):
        raise BadRequest("Puesto no válido", fields={"puesto_id": "No existe"})


@bp.get("")
@staff_permission("equipo")
def list_employees():
    availability = request.args.get("disponibilidad")
    if availability:
        rows = get_db().fetch_all(f"{EMPLOYEE_SELECT} WHERE e.fecha_baja IS NULL AND e.disponibilidad = %s "
                                  "ORDER BY u.nombre", (availability,))
    else:
        rows = get_db().fetch_all(f"{EMPLOYEE_SELECT} WHERE e.fecha_baja IS NULL ORDER BY u.nombre")
    data = [serialize_employee(row) for row in rows]
    return jsonify({"data": data, "total": len(data), "page": 1})


@bp.post("")
@staff_permission("equipo", write=True)
def create_employee():
    data = parse_body(EmployeeCreateIn)
    db = get_db()
    with db.transaction():
        ensure_unique_identity(email=data.email, dni=data.dni)
        _ensure_position(data.puesto_id)
        user_id = db.execute(
            """INSERT INTO usuarios (rol_id, nombre_usuario, nombre, apellidos, dni, email, password_hash, telefono)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (role_id(data.rol), generate_username(data.nombre, data.apellidos), data.nombre, data.apellidos,
             data.dni, data.email, hash_password(data.password_temporal), data.telefono))
        employee_id = db.execute(
            """INSERT INTO empleados (usuario_id, puesto_id, especialidad, capacidad_semanal, fecha_alta)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, data.puesto_id, data.especialidad, data.capacidad_semanal, data.fecha_alta))
    return jsonify(serialize_employee(_employee(employee_id))), 201


@bp.get("/<int:employee_id>")
@staff_permission("equipo")
def get_employee(employee_id: int):
    employee = serialize_employee(_employee(employee_id))
    jobs = get_db().fetch_all(
        f"""SELECT p.id, p.estado, p.entrega_estimada, v.marca, v.modelo, v.variante FROM pedidos p
              JOIN vehiculos v ON v.id = p.vehiculo_id
             WHERE p.responsable_id = %s AND p.estado IN ({placeholders(len(ACTIVE_WORK_STATES) + 1)})
             ORDER BY p.entrega_estimada""", (employee_id, *ACTIVE_WORK_STATES, "listo"))
    employee["trabajos"] = [{"pedido_id": job["id"], "pedido": order_code(job["id"]), "estado": job["estado"],
                             "vehiculo": " ".join(filter(None, (job["marca"], job["modelo"], job["variante"]))),
                             "entrega_estimada": job["entrega_estimada"]} for job in jobs]
    return jsonify(employee)


@bp.patch("/<int:employee_id>")
@staff_permission("equipo", write=True)
def update_employee(employee_id: int):
    employee = _employee(employee_id)
    changes = changed_fields(parse_body(EmployeeUpdateIn))
    db = get_db()
    with db.transaction():
        new_role = changes.pop("rol", None)
        if "puesto_id" in changes:
            _ensure_position(changes["puesto_id"])
        if changes:
            assignments = ", ".join(f"{column} = %s" for column in changes)
            db.execute(f"UPDATE empleados SET {assignments} WHERE id = %s", (*changes.values(), employee_id))
        if new_role:
            db.execute("UPDATE usuarios SET rol_id = %s WHERE id = %s", (role_id(new_role), employee["usuario_id"]))
    return jsonify(serialize_employee(_employee(employee_id)))


@bp.patch("/<int:employee_id>/availability")
@staff_permission("equipo", write=True)
def update_availability(employee_id: int):
    _employee(employee_id)
    data = parse_body(AvailabilityIn)
    returns_on = data.ausente_hasta if data.disponibilidad != "disponible" else None
    db = get_db()
    with db.transaction():
        db.execute("UPDATE empleados SET disponibilidad = %s, ausente_hasta = %s WHERE id = %s",
                   (data.disponibilidad, returns_on, employee_id))
    return jsonify({"id": employee_id, "disponibilidad": data.disponibilidad, "ausente_hasta": returns_on})


@bp.delete("/<int:employee_id>")
@staff_permission("equipo", write=True)
def remove_employee(employee_id: int):
    employee = _employee(employee_id)
    if employee["pedidos_activos"]:
        raise Conflict(f"Reasigna antes sus {employee['pedidos_activos']} pedidos activos", "EMPLOYEE_HAS_ORDERS")
    db = get_db()
    with db.transaction():
        db.execute("UPDATE empleados SET fecha_baja = %s, disponibilidad = 'baja' WHERE id = %s", (date.today(), employee_id))
        db.execute("UPDATE usuarios SET activo = FALSE, eliminado_en = %s WHERE id = %s", (now(), employee["usuario_id"]))
        db.execute("UPDATE citas SET empleado_id = NULL WHERE empleado_id = %s AND estado = 'programada'", (employee_id,))
    return no_content()
