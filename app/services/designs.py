import random
from datetime import datetime
from typing import Optional

from flask import current_app

from ..constants import ANGLES
from ..db import get_db
from ..errors import BadRequest, Conflict, NotFound
from ..http import json_value, to_json_text
from ..security import current_user
from ..storage import signed_url
from .settings import get_setting


def daily_limit() -> int:
    return int(get_setting("ia").get("generaciones_diarias_por_cliente", 3))


def generations_today(user_id: int) -> int:
    start_of_day = datetime.combine(datetime.now().date(), datetime.min.time())
    return get_db().fetch_value(
        "SELECT COUNT(*) FROM versiones_diseno WHERE creado_por = %s AND creado_en >= %s", (user_id, start_of_day))


def ensure_generation_allowed(user_id: int) -> int:
    used = generations_today(user_id)
    if used >= daily_limit():
        raise Conflict(f"Has llegado al límite de {daily_limit()} generaciones por hoy", "DAILY_LIMIT")
    return daily_limit() - used - 1


def provider_name() -> str:
    if get_setting("ia").get("modo_demo") or current_app.config["AI_PROVIDER"] == "demo":
        return "demo"
    return "gemini"


def get_owned_version(version_id: int) -> dict:
    version = get_db().fetch_one(
        """SELECT vd.*, p.cliente_id, p.version_actual_id FROM versiones_diseno vd
             JOIN pedidos p ON p.id = vd.pedido_id WHERE vd.id = %s""", (version_id,))
    if version is None or version["cliente_id"] != current_user()["id"]:
        raise NotFound("Versión no encontrada")
    return version


def create_version(order: dict, request_text: str, parent_id: Optional[int]) -> tuple[int, int]:
    """Crea la versión, registra qué fotos y referencias usa y la encola. Devuelve (version_id, job_id)."""
    db = get_db()
    user_id = current_user()["id"]
    photos = db.fetch_all(
        "SELECT archivo_id, angulo FROM fotos_vehiculo WHERE vehiculo_id = %s AND activa = TRUE",
        (order["vehiculo_id"],))
    if not photos:
        raise BadRequest("Sube al menos una foto del coche antes de generar", "NO_VEHICLE_PHOTOS")
    if parent_id is not None:
        parent = db.fetch_one("SELECT id, estado FROM versiones_diseno WHERE id = %s AND pedido_id = %s",
                              (parent_id, order["id"]))
        if parent is None:
            raise NotFound("La versión de partida no pertenece a este pedido")

    number = (db.fetch_value("SELECT MAX(numero) FROM versiones_diseno WHERE pedido_id = %s", (order["id"],)) or 0) + 1
    version_id = db.execute(
        """INSERT INTO versiones_diseno (pedido_id, numero, version_padre_id, peticion, estado, proveedor_ia,
                                         semilla, creado_por)
           VALUES (%s, %s, %s, %s, 'pendiente', %s, %s, %s)""",
        (order["id"], number, parent_id, request_text, provider_name(), random.randint(1, 2**31 - 1), user_id))

    for photo in photos:
        db.execute("INSERT INTO version_entradas (version_id, archivo_id, rol, angulo) VALUES (%s, %s, 'foto_vehiculo', %s)",
                   (version_id, photo["archivo_id"], photo["angulo"]))
    references = db.fetch_all(
        "SELECT id, archivo_id FROM referencias_pieza WHERE pedido_id = %s AND archivo_id IS NOT NULL", (order["id"],))
    for reference in references:
        db.execute(
            """INSERT INTO version_entradas (version_id, archivo_id, rol, referencia_id)
               VALUES (%s, %s, 'referencia_pieza', %s)""", (version_id, reference["archivo_id"], reference["id"]))
    if parent_id is not None:
        master = db.fetch_one(
            """SELECT f.archivo_id, f.angulo FROM fotogramas_360 f JOIN versiones_diseno vd ON vd.id = f.version_id
                WHERE vd.id = %s AND f.angulo = vd.vista_maestra""", (parent_id,))
        if master and not db.fetch_value("SELECT 1 FROM version_entradas WHERE version_id = %s AND archivo_id = %s",
                                         (version_id, master["archivo_id"])):
            db.execute("INSERT INTO version_entradas (version_id, archivo_id, rol, angulo) VALUES (%s, %s, 'version_previa', %s)",
                       (version_id, master["archivo_id"], master["angulo"]))
    job_id = enqueue_job("diseno_360", user_id, version_id=version_id)
    return version_id, job_id


def enqueue_job(job_type: str, user_id: int, version_id: Optional[int] = None, angle: Optional[str] = None,
                replica_id: Optional[int] = None, parameters: Optional[dict] = None, priority: int = 5) -> int:
    provider = "huggingface" if job_type == "modelo_3d" and provider_name() != "demo" else provider_name()
    return get_db().execute(
        """INSERT INTO trabajos_ia (tipo, estado, prioridad, version_id, angulo, replica_id, solicitado_por,
                                    proveedor, parametros)
           VALUES (%s, 'en_cola', %s, %s, %s, %s, %s, %s, %s)""",
        (job_type, priority, version_id, angle, replica_id, user_id, provider, to_json_text(parameters)))


def frames_of(version_id: int) -> list[dict]:
    rows = get_db().fetch_all(
        "SELECT angulo, archivo_id, origen, intento, valido FROM fotogramas_360 WHERE version_id = %s", (version_id,))
    rows.sort(key=lambda row: ANGLES.index(row["angulo"]))
    return [{"angulo": row["angulo"], "url": signed_url(row["archivo_id"]), "origen": row["origen"],
             "intento": row["intento"], "valido": bool(row["valido"])} for row in rows]


def serialize_version(version: dict, with_frames: bool = False) -> dict:
    data = {"id": version["id"], "pedido_id": version["pedido_id"], "numero": version["numero"],
            "version_padre_id": version["version_padre_id"], "peticion": version["peticion"],
            "estado": version["estado"], "vista_maestra": version["vista_maestra"],
            "creado_en": version["creado_en"], "completado_en": version["completado_en"]}
    if with_frames:
        data["plan"] = json_value(version["plan_director"])
        data["fotogramas"] = frames_of(version["id"])
        data["error"] = version["error_mensaje"]
    return data
