import hashlib
import secrets
from datetime import datetime, timezone
from functools import wraps
from typing import Callable, Optional

import jwt
from flask import current_app, g, request
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .errors import Forbidden, Unauthorized

STAFF_ROLES = ("admin", "jefe_taller", "empleado")


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return check_password_hash(password_hash, password)
    except ValueError:
        return False


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "rol": role, "iat": now,
               "exp": now + current_app.config["JWT_ACCESS_TTL"], "type": "access"}
    return jwt.encode(payload, current_app.config["JWT_SECRET"], algorithm="HS256")


def _decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError as error:
        raise Unauthorized("El token ha caducado", "TOKEN_EXPIRED") from error
    except jwt.InvalidTokenError as error:
        raise Unauthorized("Token no válido", "INVALID_TOKEN") from error
    if payload.get("type") != "access":
        raise Unauthorized("Token no válido", "INVALID_TOKEN")
    return payload


def _load_current_user() -> dict:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer ") or not header[7:].strip():
        raise Unauthorized("Falta el token o ha caducado")
    payload = _decode_access_token(header[7:].strip())
    user = get_db().fetch_one(
        """SELECT u.id, u.nombre, u.apellidos, u.email, r.codigo AS rol
             FROM usuarios u JOIN roles r ON r.id = u.rol_id
            WHERE u.id = %s AND u.activo = TRUE AND u.eliminado_en IS NULL""",
        (int(payload["sub"]),))
    if user is None:
        raise Unauthorized("La cuenta no existe o está desactivada", "ACCOUNT_DISABLED")
    return user


def current_user() -> dict:
    return g.current_user


def current_employee_id() -> Optional[int]:
    if "current_employee_id" not in g:
        g.current_employee_id = get_db().fetch_value(
            "SELECT id FROM empleados WHERE usuario_id = %s AND fecha_baja IS NULL", (current_user()["id"],))
    return g.current_employee_id


def login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapper(*args, **kwargs):
        g.current_user = _load_current_user()
        return view(*args, **kwargs)
    return wrapper


def roles_required(*roles: str) -> Callable:
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(*args, **kwargs):
            g.current_user = _load_current_user()
            if g.current_user["rol"] not in roles:
                raise Forbidden("Tu rol no tiene acceso a este recurso")
            return view(*args, **kwargs)
        return wrapper
    return decorator


def staff_permission(module: str, write: bool = False) -> Callable:
    """Exige un rol del taller con permiso sobre el módulo (tabla permisos_rol)."""
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(*args, **kwargs):
            g.current_user = _load_current_user()
            role = g.current_user["rol"]
            if role not in STAFF_ROLES:
                raise Forbidden("Tu rol no tiene acceso a este recurso")
            if role != "admin":
                permission = get_db().fetch_one(
                    """SELECT p.puede_ver, p.puede_editar FROM permisos_rol p
                         JOIN roles r ON r.id = p.rol_id WHERE r.codigo = %s AND p.modulo = %s""",
                    (role, module))
                allowed = bool(permission and (permission["puede_editar"] if write else permission["puede_ver"]))
                if not allowed:
                    raise Forbidden("Tu rol no tiene permiso para esta acción en " + module)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def is_restricted_employee() -> bool:
    """Un empleado (no jefe ni admin) solo trabaja con los pedidos que tiene asignados."""
    return current_user()["rol"] == "empleado"
