from flask import Blueprint, jsonify, request

from ...db import get_db
from ...errors import NotFound
from ...http import money

bp = Blueprint("catalog", __name__, url_prefix="")

SERVICE_SELECT = """SELECT s.id, s.nombre, s.descripcion, t.codigo AS tipo_modificacion, s.precio_base,
                           s.horas_estimadas, s.activo
                      FROM servicios s LEFT JOIN tipos_modificacion t ON t.id = s.tipo_modificacion_id"""


def serialize_service(row: dict) -> dict:
    return {**row, "precio_base": money(row["precio_base"]), "horas_estimadas": float(row["horas_estimadas"]),
            "activo": bool(row["activo"])}


@bp.get("/services")
def list_services():
    modification_type = request.args.get("tipo")
    if modification_type:
        rows = get_db().fetch_all(f"{SERVICE_SELECT} WHERE s.activo = TRUE AND t.codigo = %s ORDER BY s.nombre",
                                  (modification_type,))
    else:
        rows = get_db().fetch_all(f"{SERVICE_SELECT} WHERE s.activo = TRUE ORDER BY s.nombre")
    data = [serialize_service(row) for row in rows]
    return jsonify({"data": data, "total": len(data), "page": 1})


@bp.get("/services/<int:service_id>")
def get_service(service_id: int):
    row = get_db().fetch_one(f"{SERVICE_SELECT} WHERE s.id = %s AND s.activo = TRUE", (service_id,))
    if row is None:
        raise NotFound("Servicio no encontrado")
    return jsonify(serialize_service(row))


@bp.get("/modification-types")
def list_modification_types():
    rows = get_db().fetch_all("SELECT id, codigo, nombre, icono FROM tipos_modificacion WHERE activo = TRUE ORDER BY id")
    return jsonify({"data": rows})
