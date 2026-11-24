import re
import unicodedata
from typing import Optional

from ..db import get_db
from ..errors import BadRequest, Conflict, NotFound
from ..storage import signed_url

USER_COLUMNS = """u.id, r.codigo AS rol, u.nombre_usuario, u.nombre, u.apellidos, u.email,
                  u.telefono, u.dni, u.avatar_archivo_id, u.activo, u.ultimo_acceso, u.creado_en"""


def serialize_user(row: dict, include_admin_fields: bool = False) -> dict:
    user = {
        "id": row["id"], "rol": row["rol"], "nombre_usuario": row["nombre_usuario"],
        "nombre": row["nombre"], "apellidos": row["apellidos"], "email": row["email"],
        "telefono": row.get("telefono"), "dni": row.get("dni"),
        "avatar_url": signed_url(row.get("avatar_archivo_id")),
    }
    if include_admin_fields:
        user.update(activo=bool(row["activo"]), ultimo_acceso=row.get("ultimo_acceso"), creado_en=row.get("creado_en"))
    return user


def find_user(user_id: int) -> dict:
    user = get_db().fetch_one(
        f"SELECT {USER_COLUMNS} FROM usuarios u JOIN roles r ON r.id = u.rol_id "
        "WHERE u.id = %s AND u.eliminado_en IS NULL", (user_id,))
    if user is None:
        raise NotFound("Usuario no encontrado")
    return user


def role_id(role_code: str) -> int:
    found = get_db().fetch_value("SELECT id FROM roles WHERE codigo = %s", (role_code,))
    if found is None:
        raise BadRequest("Rol no válido", fields={"rol": "No existe"})
    return found


def ensure_unique_identity(email: Optional[str] = None, dni: Optional[str] = None,
                           username: Optional[str] = None, exclude_user_id: int = 0) -> None:
    db = get_db()
    if email and db.fetch_value("SELECT id FROM usuarios WHERE email = %s AND id <> %s", (email, exclude_user_id)):
        raise Conflict("Ya existe una cuenta con ese email", "EMAIL_EXISTS")
    if dni and db.fetch_value("SELECT id FROM usuarios WHERE dni = %s AND id <> %s", (dni, exclude_user_id)):
        raise Conflict("Ese DNI ya está registrado", "DNI_EXISTS")
    if username and db.fetch_value("SELECT id FROM usuarios WHERE nombre_usuario = %s AND id <> %s",
                                   (username, exclude_user_id)):
        raise Conflict(f"El nombre de usuario {username} ya existe", "USERNAME_EXISTS")


def _ascii_slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", normalized.lower())


def generate_username(first_name: str, surnames: str) -> str:
    first_surname = surnames.split()[0] if surnames.split() else ""
    base = (_ascii_slug(first_name)[:1] + _ascii_slug(first_surname))[:24] or "usuario"
    candidate, suffix = base, 1
    while get_db().fetch_value("SELECT id FROM usuarios WHERE nombre_usuario = %s", (candidate,)):
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def count_active_admins(excluding_user_id: int = 0) -> int:
    return get_db().fetch_value(
        """SELECT COUNT(*) FROM usuarios u JOIN roles r ON r.id = u.rol_id
            WHERE r.codigo = 'admin' AND u.activo = TRUE AND u.eliminado_en IS NULL AND u.id <> %s""",
        (excluding_user_id,))
