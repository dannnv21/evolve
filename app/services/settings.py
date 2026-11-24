from typing import Any, Optional

from ..db import get_db
from ..http import json_value, now, to_json_text

DEFAULTS: dict[str, dict] = {
    "general": {"nombre": "Evolve – Rebuild your Ride", "email": "", "telefono": "", "horario": "", "direccion": ""},
    "negocio": {"razon_social": "", "nif": "", "capacidad_maxima": 10, "plazo_medio_dias": 5, "direccion_fiscal": ""},
    "avisos_automaticos": {"cambios_estado": True, "confirmacion_presupuestos": True,
                           "alertas_almacen": True, "resumen_diario": False},
    "ia": {"generaciones_diarias_por_cliente": 3, "fotogramas_360": 8, "gasto_maximo_mensual_usd": 8,
           "modo_demo": False},
}


def get_setting(key: str) -> dict:
    stored = get_db().fetch_value("SELECT valor FROM ajustes_taller WHERE clave = %s", (key,))
    values = dict(DEFAULTS.get(key, {}))
    if stored is not None:
        values.update(json_value(stored) or {})
    return values


def update_setting(key: str, changes: dict[str, Any], updated_by: Optional[int]) -> dict:
    values = get_setting(key)
    values.update(changes)
    db = get_db()
    exists = db.fetch_value("SELECT clave FROM ajustes_taller WHERE clave = %s", (key,))
    if exists:
        db.execute("UPDATE ajustes_taller SET valor = %s, actualizado_por = %s, actualizado_en = %s WHERE clave = %s",
                   (to_json_text(values), updated_by, now(), key))
    else:
        db.execute("INSERT INTO ajustes_taller (clave, valor, actualizado_por) VALUES (%s, %s, %s)",
                   (key, to_json_text(values), updated_by))
    return values
