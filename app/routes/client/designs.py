import random

from flask import Blueprint, jsonify

from ...constants import ANGLES, FINAL_ORDER_STATES
from ...db import get_db
from ...errors import BadRequest, Conflict, NotFound
from ...http import json_value, parse_body
from ...schemas.orders import DesignRequestIn, RestoreVersionIn
from ...security import current_user, login_required, roles_required, STAFF_ROLES
from ...services.designs import (create_version, daily_limit, enqueue_job, ensure_generation_allowed,
                                 generations_today, get_owned_version, serialize_version)
from ...services.orders import get_client_order

bp = Blueprint("designs", __name__, url_prefix="")


def _ensure_order_open(order: dict) -> None:
    if order["estado"] in FINAL_ORDER_STATES:
        raise Conflict("El pedido está cerrado y no admite más diseños", "ORDER_CLOSED")


@bp.post("/orders/<int:order_id>/designs")
@roles_required("cliente")
def request_design(order_id: int):
    data = parse_body(DesignRequestIn)
    db = get_db()
    with db.transaction():
        order = get_client_order(order_id, for_update=True)
        _ensure_order_open(order)
        remaining = ensure_generation_allowed(current_user()["id"])
        version_id, job_id = create_version(order, data.peticion, data.version_padre_id)
    return jsonify({"job_id": job_id, "version_id": version_id, "estado": "pendiente",
                    "generaciones_restantes_hoy": remaining}), 202


@bp.get("/ai/jobs/<int:job_id>")
@login_required
def job_status(job_id: int):
    job = get_db().fetch_one("SELECT * FROM trabajos_ia WHERE id = %s", (job_id,))
    if job is None or (job["solicitado_por"] != current_user()["id"] and current_user()["rol"] not in STAFF_ROLES):
        raise NotFound("Trabajo no encontrado")
    phase, progress = None, 100 if job["estado"] == "completado" else 0
    if job["version_id"]:
        version_state = get_db().fetch_value("SELECT estado FROM versiones_diseno WHERE id = %s", (job["version_id"],))
        phase = {"interpretando": "interpretando_peticion", "generando": "generando_vistas",
                 "revisando": "control_calidad"}.get(version_state, version_state)
        if job["estado"] == "procesando":
            frames = get_db().fetch_value("SELECT COUNT(*) FROM fotogramas_360 WHERE version_id = %s", (job["version_id"],))
            progress = 10 + int(80 * frames / len(ANGLES))
    return jsonify({"job_id": job["id"], "tipo": job["tipo"], "estado": job["estado"], "fase": phase,
                    "progreso": progress, "version_id": job["version_id"], "replica_id": job["replica_id"],
                    "error": job["error_mensaje"], "resultado": json_value(job["resultado"])})


@bp.get("/orders/<int:order_id>/designs")
@roles_required("cliente")
def list_designs(order_id: int):
    order = get_client_order(order_id)
    versions = get_db().fetch_all("SELECT * FROM versiones_diseno WHERE pedido_id = %s ORDER BY numero", (order_id,))
    return jsonify({"version_actual_id": order["version_actual_id"],
                    "data": [serialize_version(version) for version in versions]})


@bp.get("/designs/<int:version_id>")
@roles_required("cliente")
def get_design(version_id: int):
    return jsonify(serialize_version(get_owned_version(version_id), with_frames=True))


@bp.post("/designs/<int:version_id>/regenerate")
@roles_required("cliente")
def regenerate_design(version_id: int):
    source = get_owned_version(version_id)
    db = get_db()
    with db.transaction():
        order = get_client_order(source["pedido_id"], for_update=True)
        _ensure_order_open(order)
        ensure_generation_allowed(current_user()["id"])
        new_version_id, job_id = create_version(order, source["peticion"], source["version_padre_id"])
        db.execute("UPDATE versiones_diseno SET semilla = %s WHERE id = %s",
                   (random.randint(1, 2**31 - 1), new_version_id))
    return jsonify({"job_id": job_id, "version_id": new_version_id, "estado": "pendiente"}), 202


@bp.post("/designs/<int:version_id>/frames/<angle>/regenerate")
@roles_required("cliente")
def regenerate_frame(version_id: int, angle: str):
    if angle not in ANGLES:
        raise BadRequest("Ángulo no válido", fields={"angle": "Usa uno de: " + ", ".join(ANGLES)})
    version = get_owned_version(version_id)
    if version["estado"] != "listo":
        raise Conflict("Solo se pueden regenerar fotogramas de una versión terminada", "DESIGN_NOT_READY")
    db = get_db()
    with db.transaction():
        job_id = enqueue_job("fotograma", current_user()["id"], version_id=version_id, angle=angle, priority=3)
    return jsonify({"job_id": job_id, "version_id": version_id, "angulo": angle, "estado": "pendiente"}), 202


@bp.post("/orders/<int:order_id>/designs/restore")
@roles_required("cliente")
def restore_design(order_id: int):
    data = parse_body(RestoreVersionIn)
    db = get_db()
    with db.transaction():
        get_client_order(order_id, for_update=True)
        version_state = db.fetch_value("SELECT estado FROM versiones_diseno WHERE id = %s AND pedido_id = %s",
                                       (data.version_id, order_id))
        if version_state is None:
            raise NotFound("La versión no pertenece a este pedido")
        if version_state != "listo":
            raise Conflict("Solo se puede volver a una versión terminada", "DESIGN_NOT_READY")
        db.execute("UPDATE pedidos SET version_actual_id = %s WHERE id = %s", (data.version_id, order_id))
    return jsonify({"pedido_id": order_id, "version_actual_id": data.version_id})


@bp.get("/ai/usage")
@roles_required("cliente")
def ai_usage():
    used = generations_today(current_user()["id"])
    limit = daily_limit()
    return jsonify({"limite_diario": limit, "usadas_hoy": used, "restantes": max(limit - used, 0)})
