from flask import Blueprint, jsonify, request

from ...db import get_db
from ...errors import NotFound
from ...http import no_content, now, parse_body
from ...schemas.accounts import DeviceIn
from ...security import current_user, login_required
from ...services.settings import get_setting

bp = Blueprint("notifications", __name__, url_prefix="")


@bp.get("/notifications")
@login_required
def list_notifications():
    user_id = current_user()["id"]
    only_unread = request.args.get("unread", "").lower() in ("1", "true")
    sql = """SELECT id, tipo, titulo, cuerpo, pedido_id, pieza_id, replica_id, leida_en, creado_en
               FROM notificaciones WHERE usuario_id = %s"""
    if only_unread:
        sql += " AND leida_en IS NULL"
    rows = get_db().fetch_all(sql + " ORDER BY creado_en DESC, id DESC LIMIT 100", (user_id,))
    unread = get_db().fetch_value("SELECT COUNT(*) FROM notificaciones WHERE usuario_id = %s AND leida_en IS NULL",
                                  (user_id,))
    data = [{**{key: value for key, value in row.items() if key != "leida_en"}, "leida": row["leida_en"] is not None}
            for row in rows]
    return jsonify({"no_leidas": unread, "data": data})


@bp.patch("/notifications/<int:notification_id>/read")
@login_required
def mark_read(notification_id: int):
    db = get_db()
    with db.transaction():
        exists = db.fetch_value("SELECT id FROM notificaciones WHERE id = %s AND usuario_id = %s",
                                (notification_id, current_user()["id"]))
        if not exists:
            raise NotFound("Notificación no encontrada")
        db.execute("UPDATE notificaciones SET leida_en = %s WHERE id = %s AND leida_en IS NULL", (now(), notification_id))
    return no_content()


@bp.post("/notifications/read-all")
@login_required
def mark_all_read():
    db = get_db()
    with db.transaction():
        marked = db.execute_rowcount(
            "UPDATE notificaciones SET leida_en = %s WHERE usuario_id = %s AND leida_en IS NULL",
            (now(), current_user()["id"]))
    return jsonify({"marcadas": marked})


@bp.post("/devices")
@login_required
def register_device():
    data = parse_body(DeviceIn)
    db = get_db()
    with db.transaction():
        db.execute("DELETE FROM dispositivos WHERE token_push = %s", (data.token_push,))
        device_id = db.execute("INSERT INTO dispositivos (usuario_id, token_push, plataforma) VALUES (%s, %s, %s)",
                               (current_user()["id"], data.token_push, data.plataforma))
    return jsonify({"id": device_id, "plataforma": data.plataforma}), 201


@bp.delete("/devices/<path:token>")
@login_required
def remove_device(token: str):
    db = get_db()
    with db.transaction():
        db.execute("DELETE FROM dispositivos WHERE token_push = %s AND usuario_id = %s", (token, current_user()["id"]))
    return no_content()


@bp.get("/workshop")
def workshop_info():
    general = get_setting("general")
    return jsonify({"nombre": general.get("nombre"), "email": general.get("email"),
                    "telefono": general.get("telefono"), "horario": general.get("horario"),
                    "direccion": general.get("direccion"), "coordenadas": general.get("coordenadas")})
