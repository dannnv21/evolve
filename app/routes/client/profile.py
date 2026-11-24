from flask import Blueprint, jsonify, request

from ...constants import NOTIFICATION_TYPES
from ...db import get_db
from ...errors import BadRequest
from ...http import no_content, parse_body
from ...schemas.accounts import NotificationPreferencesIn, PasswordChangeIn, ProfileUpdateIn
from ...schemas.workshop import changed_fields
from ...security import current_user, hash_password, login_required, verify_password
from ...services.accounts import ensure_unique_identity, find_user, serialize_user
from ...storage import save_image_upload, signed_url

bp = Blueprint("profile", __name__, url_prefix="/me")


@bp.get("/profile")
@login_required
def get_profile():
    return jsonify(serialize_user(find_user(current_user()["id"])))


@bp.patch("/profile")
@login_required
def update_profile():
    changes = changed_fields(parse_body(ProfileUpdateIn))
    user_id = current_user()["id"]
    if changes:
        db = get_db()
        with db.transaction():
            ensure_unique_identity(email=changes.get("email"), dni=changes.get("dni"), exclude_user_id=user_id)
            assignments = ", ".join(f"{column} = %s" for column in changes)
            db.execute(f"UPDATE usuarios SET {assignments} WHERE id = %s", (*changes.values(), user_id))
    return jsonify(serialize_user(find_user(user_id)))


@bp.post("/avatar")
@login_required
def upload_avatar():
    user_id = current_user()["id"]
    db = get_db()
    with db.transaction():
        file_id = save_image_upload(request.files.get("archivo"), f"usuarios/{user_id}", user_id)
        db.execute("UPDATE usuarios SET avatar_archivo_id = %s WHERE id = %s", (file_id, user_id))
    return jsonify({"avatar_url": signed_url(file_id)})


@bp.put("/password")
@login_required
def change_password():
    data = parse_body(PasswordChangeIn)
    db = get_db()
    user_id = current_user()["id"]
    stored_hash = db.fetch_value("SELECT password_hash FROM usuarios WHERE id = %s", (user_id,))
    if not verify_password(stored_hash, data.password_actual):
        raise BadRequest("La contraseña actual no es correcta", "WRONG_PASSWORD")
    with db.transaction():
        db.execute("UPDATE usuarios SET password_hash = %s WHERE id = %s", (hash_password(data.password_nueva), user_id))
    return no_content()


@bp.get("/notification-preferences")
@login_required
def get_notification_preferences():
    stored = {row["tipo"]: row for row in get_db().fetch_all(
        "SELECT tipo, push, email FROM preferencias_notificacion WHERE usuario_id = %s", (current_user()["id"],))}
    preferences = [{"tipo": kind, "push": bool(stored[kind]["push"]) if kind in stored else True,
                    "email": bool(stored[kind]["email"]) if kind in stored else False}
                   for kind in NOTIFICATION_TYPES]
    return jsonify({"data": preferences})


@bp.put("/notification-preferences")
@login_required
def save_notification_preferences():
    data = parse_body(NotificationPreferencesIn)
    db = get_db()
    user_id = current_user()["id"]
    with db.transaction():
        for preference in data.preferencias:
            db.execute("DELETE FROM preferencias_notificacion WHERE usuario_id = %s AND tipo = %s",
                       (user_id, preference.tipo))
            db.execute("INSERT INTO preferencias_notificacion (usuario_id, tipo, push, email) VALUES (%s, %s, %s, %s)",
                       (user_id, preference.tipo, preference.push, preference.email))
    return jsonify({"actualizadas": len(data.preferencias)})
