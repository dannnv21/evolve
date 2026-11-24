from flask import Blueprint, jsonify

from ...constants import ACTIVE_WORK_STATES, PERMISSION_MODULES
from ...db import get_db, placeholders
from ...http import parse_body
from ...schemas.workshop import AutomaticNoticesIn, BusinessSettingsIn, GeneralSettingsIn, PermissionsIn, changed_fields
from ...security import current_user, staff_permission
from ...services.accounts import role_id
from ...services.settings import get_setting, update_setting

bp = Blueprint("admin_settings", __name__, url_prefix="/admin/settings")

LEVEL_TO_FLAGS = {"ninguno": (False, False), "ver": (True, False), "editar": (True, True)}


def _with_occupancy(values: dict) -> dict:
    occupied = get_db().fetch_value(
        f"SELECT COUNT(*) FROM pedidos WHERE estado IN ({placeholders(len(ACTIVE_WORK_STATES) + 1)})",
        (*ACTIVE_WORK_STATES, "listo"))
    return {**values, "ocupacion_actual": occupied}


def _save(key: str, schema):
    db = get_db()
    with db.transaction():
        return update_setting(key, changed_fields(parse_body(schema)), current_user()["id"])


@bp.get("/general")
@staff_permission("configuracion")
def get_general():
    return jsonify(get_setting("general"))


@bp.put("/general")
@staff_permission("configuracion", write=True)
def save_general():
    return jsonify(_save("general", GeneralSettingsIn))


@bp.get("/business")
@staff_permission("configuracion")
def get_business():
    return jsonify(_with_occupancy(get_setting("negocio")))


@bp.put("/business")
@staff_permission("configuracion", write=True)
def save_business():
    return jsonify(_with_occupancy(_save("negocio", BusinessSettingsIn)))


@bp.get("/notifications")
@staff_permission("configuracion")
def get_automatic_notices():
    return jsonify(get_setting("avisos_automaticos"))


@bp.put("/notifications")
@staff_permission("configuracion", write=True)
def save_automatic_notices():
    return jsonify(_save("avisos_automaticos", AutomaticNoticesIn))


def _permission_matrix() -> list[dict]:
    rows = get_db().fetch_all(
        """SELECT r.codigo AS rol, p.modulo, p.puede_ver, p.puede_editar
             FROM permisos_rol p JOIN roles r ON r.id = p.rol_id
            WHERE r.codigo IN ('jefe_taller', 'empleado') ORDER BY r.id""")
    matrix: dict[str, dict] = {}
    for row in rows:
        level = "editar" if row["puede_editar"] else "ver" if row["puede_ver"] else "ninguno"
        matrix.setdefault(row["rol"], {"rol": row["rol"]})[row["modulo"]] = level
    for role_permissions in matrix.values():
        for module in PERMISSION_MODULES:
            role_permissions.setdefault(module, "ninguno")
    return list(matrix.values())


@bp.get("/permissions")
@staff_permission("configuracion")
def get_permissions():
    return jsonify({"roles": _permission_matrix(), "nota": "El administrador siempre tiene acceso completo"})


@bp.put("/permissions")
@staff_permission("configuracion", write=True)
def save_permissions():
    data = parse_body(PermissionsIn)
    db = get_db()
    updated = 0
    with db.transaction():
        for role_permissions in data.roles:
            target_role = role_id(role_permissions.rol)
            for module, level in changed_fields(role_permissions).items():
                if module == "rol" or level is None:
                    continue
                can_view, can_edit = LEVEL_TO_FLAGS[level]
                db.execute("DELETE FROM permisos_rol WHERE rol_id = %s AND modulo = %s", (target_role, module))
                db.execute("INSERT INTO permisos_rol (rol_id, modulo, puede_ver, puede_editar) VALUES (%s, %s, %s, %s)",
                           (target_role, module, can_view, can_edit))
                updated += 1
    return jsonify({"actualizados": updated, "roles": _permission_matrix()})
