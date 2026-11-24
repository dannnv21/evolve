import secrets

from flask import Blueprint, jsonify

from ...db import get_db
from ...errors import Conflict
from ...http import no_content, now, pagination, paginated, parse_body, parse_query
from ...schemas.accounts import UserCreateIn, UserListQuery, UserStatusIn, UserUpdateIn
from ...schemas.workshop import changed_fields
from ...security import current_user, hash_password, staff_permission
from ...services.accounts import (USER_COLUMNS, count_active_admins, ensure_unique_identity, find_user,
                                  generate_username, role_id, serialize_user)

bp = Blueprint("admin_users", __name__, url_prefix="/admin/users")


def _protect_last_admin(user: dict) -> None:
    if user["rol"] == "admin" and count_active_admins(excluding_user_id=user["id"]) == 0:
        raise Conflict("No puedes dejar el sistema sin ningún administrador activo", "LAST_ADMIN")


@bp.get("")
@staff_permission("cuentas")
def list_users():
    query = parse_query(UserListQuery)
    page, limit, offset = pagination()
    conditions, params = ["u.eliminado_en IS NULL"], []
    if query.rol:
        conditions.append("r.codigo = %s")
        params.append(query.rol)
    if query.activo is not None:
        conditions.append("u.activo = %s")
        params.append(query.activo)
    if query.q:
        pattern = f"%{query.q.lower()}%"
        conditions.append("(LOWER(u.nombre) LIKE %s OR LOWER(u.apellidos) LIKE %s OR LOWER(u.email) LIKE %s "
                          "OR LOWER(u.nombre_usuario) LIKE %s)")
        params += [pattern] * 4
    where = " AND ".join(conditions)
    db = get_db()
    total = db.fetch_value(f"SELECT COUNT(*) FROM usuarios u JOIN roles r ON r.id = u.rol_id WHERE {where}", params)
    rows = db.fetch_all(f"SELECT {USER_COLUMNS} FROM usuarios u JOIN roles r ON r.id = u.rol_id WHERE {where} "
                        f"ORDER BY u.nombre, u.apellidos LIMIT {limit} OFFSET {offset}", params)
    return paginated([serialize_user(row, include_admin_fields=True) for row in rows], total, page)


@bp.post("")
@staff_permission("cuentas", write=True)
def create_user():
    data = parse_body(UserCreateIn)
    db = get_db()
    with db.transaction():
        username = data.nombre_usuario or generate_username(data.nombre, data.apellidos)
        ensure_unique_identity(email=data.email, dni=data.dni, username=username)
        user_id = db.execute(
            """INSERT INTO usuarios (rol_id, nombre_usuario, nombre, apellidos, dni, email, password_hash, telefono, activo)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (role_id(data.rol), username, data.nombre, data.apellidos, data.dni, data.email,
             hash_password(data.password_temporal), data.telefono, data.activo))
    return jsonify(serialize_user(find_user(user_id), include_admin_fields=True)), 201


@bp.get("/<int:user_id>")
@staff_permission("cuentas")
def get_user(user_id: int):
    return jsonify(serialize_user(find_user(user_id), include_admin_fields=True))


@bp.patch("/<int:user_id>")
@staff_permission("cuentas", write=True)
def update_user(user_id: int):
    user = find_user(user_id)
    changes = changed_fields(parse_body(UserUpdateIn))
    db = get_db()
    with db.transaction():
        ensure_unique_identity(email=changes.get("email"), dni=changes.get("dni"),
                               username=changes.get("nombre_usuario"), exclude_user_id=user_id)
        new_role = changes.pop("rol", None)
        if new_role and new_role != user["rol"]:
            _protect_last_admin(user)
            changes["rol_id"] = role_id(new_role)
        if changes:
            assignments = ", ".join(f"{column} = %s" for column in changes)
            db.execute(f"UPDATE usuarios SET {assignments} WHERE id = %s", (*changes.values(), user_id))
    return jsonify(serialize_user(find_user(user_id), include_admin_fields=True))


@bp.patch("/<int:user_id>/status")
@staff_permission("cuentas", write=True)
def set_user_status(user_id: int):
    user = find_user(user_id)
    data = parse_body(UserStatusIn)
    db = get_db()
    with db.transaction():
        if not data.activo:
            _protect_last_admin(user)
            db.execute("UPDATE tokens_usuario SET usado_en = %s WHERE usuario_id = %s AND usado_en IS NULL",
                       (now(), user_id))
        db.execute("UPDATE usuarios SET activo = %s WHERE id = %s", (data.activo, user_id))
    return jsonify({"id": user_id, "activo": data.activo})


@bp.post("/<int:user_id>/reset-password")
@staff_permission("cuentas", write=True)
def reset_user_password(user_id: int):
    find_user(user_id)
    temporary_password = f"Tmp-{secrets.token_hex(2).upper()}-{secrets.randbelow(90) + 10}x"
    db = get_db()
    with db.transaction():
        db.execute("UPDATE usuarios SET password_hash = %s WHERE id = %s", (hash_password(temporary_password), user_id))
        db.execute("UPDATE tokens_usuario SET usado_en = %s WHERE usuario_id = %s AND usado_en IS NULL", (now(), user_id))
    return jsonify({"password_temporal": temporary_password, "debe_cambiarla": True})


@bp.delete("/<int:user_id>")
@staff_permission("cuentas", write=True)
def delete_user(user_id: int):
    user = find_user(user_id)
    if user_id == current_user()["id"]:
        raise Conflict("No puedes eliminar tu propia cuenta", "SELF_DELETE")
    _protect_last_admin(user)
    db = get_db()
    with db.transaction():
        db.execute("UPDATE usuarios SET activo = FALSE, eliminado_en = %s WHERE id = %s", (now(), user_id))
        db.execute("UPDATE tokens_usuario SET usado_en = %s WHERE usuario_id = %s AND usado_en IS NULL", (now(), user_id))
    return no_content()
