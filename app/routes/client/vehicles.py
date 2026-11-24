from flask import Blueprint, jsonify, request

from ...constants import ANGLES, FINAL_ORDER_STATES
from ...db import get_db, placeholders
from ...errors import Conflict, NotFound
from ...http import no_content, parse_body
from ...schemas.orders import PhotoAngleIn, VehicleIn, VehicleUpdateIn
from ...schemas.workshop import changed_fields
from ...security import current_user, roles_required
from ...storage import save_image_upload, signed_url

bp = Blueprint("vehicles", __name__, url_prefix="/vehicles")

VEHICLE_COLUMNS = "id, marca, modelo, variante, anio, matricula, color"


def _owned_vehicle(vehicle_id: int) -> dict:
    vehicle = get_db().fetch_one(
        f"SELECT {VEHICLE_COLUMNS} FROM vehiculos WHERE id = %s AND propietario_id = %s AND activo = TRUE",
        (vehicle_id, current_user()["id"]))
    if vehicle is None:
        raise NotFound("Vehículo no encontrado")
    return vehicle


def _ensure_plate_free(plate: str, exclude_vehicle_id: int = 0) -> None:
    if get_db().fetch_value("SELECT id FROM vehiculos WHERE matricula = %s AND id <> %s", (plate, exclude_vehicle_id)):
        raise Conflict("Ya hay un vehículo con esa matrícula", "PLATE_EXISTS")


def _photos(vehicle_id: int) -> list[dict]:
    rows = get_db().fetch_all(
        "SELECT id, angulo, archivo_id FROM fotos_vehiculo WHERE vehiculo_id = %s AND activa = TRUE", (vehicle_id,))
    rows.sort(key=lambda row: ANGLES.index(row["angulo"]))
    return [{"id": row["id"], "angulo": row["angulo"], "url": signed_url(row["archivo_id"])} for row in rows]


@bp.get("")
@roles_required("cliente")
def list_vehicles():
    rows = get_db().fetch_all(
        f"SELECT {VEHICLE_COLUMNS} FROM vehiculos WHERE propietario_id = %s AND activo = TRUE ORDER BY creado_en DESC",
        (current_user()["id"],))
    return jsonify({"data": rows, "total": len(rows), "page": 1})


@bp.post("")
@roles_required("cliente")
def create_vehicle():
    data = parse_body(VehicleIn)
    db = get_db()
    with db.transaction():
        _ensure_plate_free(data.matricula)
        vehicle_id = db.execute(
            """INSERT INTO vehiculos (propietario_id, marca, modelo, variante, anio, matricula, color)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (current_user()["id"], data.marca, data.modelo, data.variante, data.anio, data.matricula, data.color))
    return jsonify(_owned_vehicle(vehicle_id)), 201


@bp.get("/<int:vehicle_id>")
@roles_required("cliente")
def get_vehicle(vehicle_id: int):
    return jsonify({**_owned_vehicle(vehicle_id), "fotos": _photos(vehicle_id)})


@bp.patch("/<int:vehicle_id>")
@roles_required("cliente")
def update_vehicle(vehicle_id: int):
    _owned_vehicle(vehicle_id)
    changes = changed_fields(parse_body(VehicleUpdateIn))
    if changes:
        db = get_db()
        with db.transaction():
            if "matricula" in changes:
                _ensure_plate_free(changes["matricula"], vehicle_id)
            assignments = ", ".join(f"{column} = %s" for column in changes)
            db.execute(f"UPDATE vehiculos SET {assignments} WHERE id = %s", (*changes.values(), vehicle_id))
    return jsonify(_owned_vehicle(vehicle_id))


@bp.delete("/<int:vehicle_id>")
@roles_required("cliente")
def delete_vehicle(vehicle_id: int):
    _owned_vehicle(vehicle_id)
    db = get_db()
    open_orders = db.fetch_value(
        f"SELECT COUNT(*) FROM pedidos WHERE vehiculo_id = %s AND estado NOT IN ({placeholders(len(FINAL_ORDER_STATES) + 1)})",
        (vehicle_id, *FINAL_ORDER_STATES, "borrador"))
    if open_orders:
        raise Conflict("No se puede borrar: tiene un pedido en curso", "VEHICLE_IN_USE")
    with db.transaction():
        if db.fetch_value("SELECT COUNT(*) FROM pedidos WHERE vehiculo_id = %s", (vehicle_id,)):
            db.execute("UPDATE vehiculos SET activo = FALSE WHERE id = %s", (vehicle_id,))
        else:
            db.execute("DELETE FROM vehiculos WHERE id = %s", (vehicle_id,))
    return no_content()


@bp.post("/<int:vehicle_id>/photos")
@roles_required("cliente")
def upload_photo(vehicle_id: int):
    _owned_vehicle(vehicle_id)
    angle = PhotoAngleIn.model_validate({"angulo": request.form.get("angulo")}).angulo
    db = get_db()
    user_id = current_user()["id"]
    with db.transaction():
        file_id = save_image_upload(request.files.get("archivo"), f"vehiculos/{vehicle_id}", user_id)
        db.execute("UPDATE fotos_vehiculo SET activa = FALSE WHERE vehiculo_id = %s AND angulo = %s",
                   (vehicle_id, angle))
        photo_id = db.execute("INSERT INTO fotos_vehiculo (vehiculo_id, archivo_id, angulo) VALUES (%s, %s, %s)",
                              (vehicle_id, file_id, angle))
    return jsonify({"id": photo_id, "angulo": angle, "url": signed_url(file_id)}), 201


@bp.delete("/<int:vehicle_id>/photos/<int:photo_id>")
@roles_required("cliente")
def delete_photo(vehicle_id: int, photo_id: int):
    _owned_vehicle(vehicle_id)
    db = get_db()
    with db.transaction():
        updated = db.execute_rowcount(
            "UPDATE fotos_vehiculo SET activa = FALSE WHERE id = %s AND vehiculo_id = %s AND activa = TRUE",
            (photo_id, vehicle_id))
    if not updated:
        raise NotFound("Foto no encontrada")
    return no_content()
