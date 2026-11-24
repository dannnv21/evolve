from flask import Blueprint, jsonify, request

from ...db import get_db
from ...errors import BadRequest, Conflict, NotFound
from ...http import parse_body
from ...schemas.workshop import ActiveFlagIn, ServiceIn, ServiceUpdateIn, changed_fields
from ...security import staff_permission
from ..client.catalog import SERVICE_SELECT, serialize_service

bp = Blueprint("admin_services", __name__, url_prefix="/admin/services")


def _service(service_id: int) -> dict:
    row = get_db().fetch_one(f"{SERVICE_SELECT} WHERE s.id = %s", (service_id,))
    if row is None:
        raise NotFound("Servicio no encontrado")
    return serialize_service(row)


def _validate(name=None, type_id=None, exclude_id: int = 0) -> None:
    db = get_db()
    if name and db.fetch_value("SELECT id FROM servicios WHERE LOWER(nombre) = LOWER(%s) AND id <> %s", (name, exclude_id)):
        raise Conflict("Ya existe un servicio con ese nombre", "SERVICE_EXISTS")
    if type_id and not db.fetch_value("SELECT id FROM tipos_modificacion WHERE id = %s", (type_id,)):
        raise BadRequest("Tipo de modificación no válido", fields={"tipo_modificacion_id": "No existe"})


@bp.get("")
@staff_permission("pedidos")
def list_services():
    active = request.args.get("activo", "").lower()
    if active in ("true", "false"):
        rows = get_db().fetch_all(f"{SERVICE_SELECT} WHERE s.activo = %s ORDER BY s.nombre", (active == "true",))
    else:
        rows = get_db().fetch_all(f"{SERVICE_SELECT} ORDER BY s.nombre")
    data = [serialize_service(row) for row in rows]
    return jsonify({"data": data, "total": len(data), "page": 1})


@bp.post("")
@staff_permission("configuracion", write=True)
def create_service():
    data = parse_body(ServiceIn)
    db = get_db()
    with db.transaction():
        _validate(data.nombre, data.tipo_modificacion_id)
        service_id = db.execute(
            """INSERT INTO servicios (tipo_modificacion_id, nombre, descripcion, precio_base, horas_estimadas)
               VALUES (%s, %s, %s, %s, %s)""",
            (data.tipo_modificacion_id, data.nombre, data.descripcion, data.precio_base, data.horas_estimadas))
    return jsonify(_service(service_id)), 201


@bp.patch("/<int:service_id>")
@staff_permission("configuracion", write=True)
def update_service(service_id: int):
    _service(service_id)
    changes = changed_fields(parse_body(ServiceUpdateIn))
    db = get_db()
    with db.transaction():
        _validate(changes.get("nombre"), changes.get("tipo_modificacion_id"), service_id)
        if changes:
            assignments = ", ".join(f"{column} = %s" for column in changes)
            db.execute(f"UPDATE servicios SET {assignments} WHERE id = %s", (*changes.values(), service_id))
    return jsonify(_service(service_id))


@bp.patch("/<int:service_id>/status")
@staff_permission("configuracion", write=True)
def set_service_status(service_id: int):
    _service(service_id)
    data = parse_body(ActiveFlagIn)
    db = get_db()
    with db.transaction():
        db.execute("UPDATE servicios SET activo = %s WHERE id = %s", (data.activo, service_id))
    return jsonify({"id": service_id, "activo": data.activo})
