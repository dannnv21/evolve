"""Worker de la cola trabajos_ia.

Se ejecuta aparte de la API:  flask --app run ai-worker
Cada trabajo se reserva con SELECT ... FOR UPDATE SKIP LOCKED, así que
pueden funcionar varios workers a la vez sin coger el mismo trabajo.
"""
import logging
import os
import socket
import time
from datetime import timedelta
from typing import Optional

from flask import current_app

from ..constants import ANGLES
from ..db import get_db
from ..http import json_value, now, order_code, to_json_text
from ..services.notifications import notify
from ..storage import read_file, save_bytes
from .models3d import Demo3DProvider
from .providers import DesignInputs, InputImage, ProviderError, build_image_provider

logger = logging.getLogger(__name__)
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
EXTENSIONS = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg"}


def claim_next_job() -> Optional[dict]:
    db = get_db()
    with db.transaction():
        job = db.fetch_one(
            """SELECT * FROM trabajos_ia WHERE estado = 'en_cola' AND disponible_desde <= %s
                ORDER BY prioridad, id LIMIT 1 FOR UPDATE SKIP LOCKED""", (now(),))
        if job is None:
            return None
        db.execute("""UPDATE trabajos_ia SET estado = 'procesando', intentos = intentos + 1, iniciado_en = %s,
                             bloqueado_por = %s WHERE id = %s""", (now(), WORKER_ID, job["id"]))
    job["intentos"] += 1
    return job


def _load_inputs(version: dict) -> DesignInputs:
    db = get_db()
    rows = db.fetch_all(
        """SELECT ve.rol, ve.angulo, a.*, r.modelo AS referencia_modelo, t.nombre AS referencia_tipo
             FROM version_entradas ve
             JOIN archivos a ON a.id = ve.archivo_id
             LEFT JOIN referencias_pieza r ON r.id = ve.referencia_id
             LEFT JOIN tipos_modificacion t ON t.id = r.tipo_modificacion_id
            WHERE ve.version_id = %s""", (version["id"],))
    images = [InputImage(data=read_file(row), mime=row["mime"], role=row["rol"], angle=row["angulo"],
                         label=row["referencia_modelo"] or row["referencia_tipo"]) for row in rows]
    return DesignInputs(request_text=version["peticion"], images=images)


def _save_frame(version: dict, angle: str, data: bytes, mime: str, origin: str) -> int:
    db = get_db()
    file_id = save_bytes(data, f"pedidos/{version['pedido_id']}/v{version['numero']}", mime,
                         EXTENSIONS.get(mime, ".png"), original_name=f"{angle}{EXTENSIONS.get(mime, '.png')}")
    previous_attempt = db.fetch_value("SELECT intento FROM fotogramas_360 WHERE version_id = %s AND angulo = %s",
                                      (version["id"], angle))
    if previous_attempt:
        db.execute("UPDATE fotogramas_360 SET archivo_id = %s, intento = %s, valido = TRUE WHERE version_id = %s AND angulo = %s",
                   (file_id, previous_attempt + 1, version["id"], angle))
    else:
        db.execute("INSERT INTO fotogramas_360 (version_id, angulo, archivo_id, origen) VALUES (%s, %s, %s, %s)",
                   (version["id"], angle, file_id, origin))
    return file_id


def _set_version(version_id: int, **fields) -> None:
    db = get_db()
    assignments = ", ".join(f"{column} = %s" for column in fields)
    with db.transaction():
        db.execute(f"UPDATE versiones_diseno SET {assignments} WHERE id = %s", (*fields.values(), version_id))


def process_design(job: dict) -> dict:
    db = get_db()
    version = db.fetch_one("SELECT * FROM versiones_diseno WHERE id = %s", (job["version_id"],))
    provider = build_image_provider(current_app.config, job["proveedor"])
    inputs = _load_inputs(version)

    _set_version(version["id"], estado="interpretando", modelo_texto=provider.text_model,
                 modelo_imagen=provider.image_model)
    plan = provider.plan(inputs)
    master_angle = plan["vista_maestra"]
    _set_version(version["id"], estado="generando", plan_director=to_json_text(plan), vista_maestra=master_angle)

    master_data, master_mime = provider.render_frame(master_angle, plan, inputs, master=None)
    with db.transaction():
        _save_frame(version, master_angle, master_data, master_mime,
                    "editada" if inputs.vehicle_photo(master_angle) else "generada")
    master = InputImage(master_data, master_mime, "version_previa", master_angle)
    for angle in ANGLES:
        if angle == master_angle:
            continue
        data, mime = provider.render_frame(angle, plan, inputs, master=master)
        with db.transaction():
            _save_frame(version, angle, data, mime, "editada" if inputs.vehicle_photo(angle) else "generada")

    _set_version(version["id"], estado="revisando")
    with db.transaction():
        db.execute("UPDATE versiones_diseno SET estado = 'listo', completado_en = %s WHERE id = %s",
                   (now(), version["id"]))
        db.execute("UPDATE pedidos SET version_actual_id = %s WHERE id = %s", (version["id"], version["pedido_id"]))
        client_id = db.fetch_value("SELECT cliente_id FROM pedidos WHERE id = %s", (version["pedido_id"],))
        notify(client_id, "diseno_listo", f"Tu diseño v{version['numero']} está listo",
               f"Ya puedes ver el 360 de {order_code(version['pedido_id'])}", order_id=version["pedido_id"])
    return {"fotogramas": len(ANGLES), "vista_maestra": master_angle}


def process_frame(job: dict) -> dict:
    db = get_db()
    version = db.fetch_one("SELECT * FROM versiones_diseno WHERE id = %s", (job["version_id"],))
    provider = build_image_provider(current_app.config, job["proveedor"])
    inputs = _load_inputs(version)
    plan = json_value(version["plan_director"]) or provider.plan(inputs)
    master_row = db.fetch_one(
        """SELECT a.* FROM fotogramas_360 f JOIN archivos a ON a.id = f.archivo_id
            WHERE f.version_id = %s AND f.angulo = %s""", (version["id"], version["vista_maestra"]))
    master = (InputImage(read_file(master_row), master_row["mime"], "version_previa", version["vista_maestra"])
              if master_row and job["angulo"] != version["vista_maestra"] else None)
    data, mime = provider.render_frame(job["angulo"], plan, inputs, master=master)
    with db.transaction():
        _save_frame(version, job["angulo"], data, mime, "editada" if inputs.vehicle_photo(job["angulo"]) else "generada")
    return {"angulo": job["angulo"]}


def process_replica(job: dict) -> dict:
    db = get_db()
    replica = db.fetch_one("SELECT * FROM replicas WHERE id = %s", (job["replica_id"],))
    if replica is None or replica["estado"] == "cancelada":
        return {"omitido": "réplica cancelada"}
    master_angle = db.fetch_value("SELECT vista_maestra FROM versiones_diseno WHERE id = %s", (replica["version_id"],))
    frames = db.fetch_all(
        """SELECT a.*, f.angulo FROM fotogramas_360 f JOIN archivos a ON a.id = f.archivo_id
            WHERE f.version_id = %s""", (replica["version_id"],))
    frames.sort(key=lambda row: (row["angulo"] != master_angle, ANGLES.index(row["angulo"])))
    model = Demo3DProvider().generate([read_file(frame) for frame in frames], replica["escala"])
    prefix = f"replicas/{replica['id']}"
    with db.transaction():
        glb_id = save_bytes(model.glb, prefix, "model/gltf-binary", ".glb", original_name="modelo.glb")
        stl_id = save_bytes(model.stl, prefix, "model/stl", ".stl", original_name="modelo.stl")
        preview_id = save_bytes(model.preview, prefix, "image/webp", ".webp", original_name="preview.webp")
        db.execute("""UPDATE replicas SET estado = 'modelo_listo', modelo_glb_id = %s, modelo_stl_id = %s,
                             preview_id = %s, motor_3d = 'trellis' WHERE id = %s AND estado = 'generando_modelo'""",
                   (glb_id, stl_id, preview_id, replica["id"]))
        notify(replica["cliente_id"], "replica", f"Tu réplica {replica['escala']} está lista para revisar",
               order_id=replica["pedido_id"], replica_id=replica["id"])
    return {"glb": glb_id, "stl": stl_id}


HANDLERS = {"diseno_360": process_design, "fotograma": process_frame, "modelo_3d": process_replica}


def _fail(job: dict, error: Exception) -> None:
    db = get_db()
    retry = job["intentos"] < job["max_intentos"] and isinstance(error, ProviderError)
    with db.transaction():
        if retry:
            db.execute("""UPDATE trabajos_ia SET estado = 'en_cola', error_mensaje = %s, bloqueado_por = NULL,
                                 disponible_desde = %s WHERE id = %s""",
                       (str(error)[:500], now() + timedelta(seconds=30 * 2 ** job["intentos"]), job["id"]))
            return
        db.execute("UPDATE trabajos_ia SET estado = 'error', error_mensaje = %s, finalizado_en = %s WHERE id = %s",
                   (str(error)[:500], now(), job["id"]))
        if job["tipo"] == "diseno_360":
            db.execute("UPDATE versiones_diseno SET estado = 'error', error_mensaje = %s WHERE id = %s",
                       (str(error)[:500], job["version_id"]))
        if job["tipo"] == "modelo_3d":
            db.execute("UPDATE replicas SET estado = 'error' WHERE id = %s", (job["replica_id"],))


def process_job(job: dict) -> None:
    try:
        result = HANDLERS[job["tipo"]](job)
    except Exception as error:   # noqa: BLE001 - cualquier fallo se registra en el trabajo
        get_db().rollback()
        logger.exception("Trabajo %s falló", job["id"])
        _fail(job, error)
        return
    db = get_db()
    with db.transaction():
        db.execute("UPDATE trabajos_ia SET estado = 'completado', resultado = %s, finalizado_en = %s, "
                   "error_mensaje = NULL WHERE id = %s", (to_json_text(result), now(), job["id"]))


def process_pending_jobs(limit: int = 50) -> int:
    processed = 0
    while processed < limit:
        job = claim_next_job()
        if job is None:
            break
        process_job(job)
        processed += 1
    return processed


def run_worker(once: bool = False) -> None:
    app = current_app._get_current_object()
    logger.info("Worker de IA %s iniciado (proveedor: %s)", WORKER_ID, app.config["AI_PROVIDER"])
    while True:
        with app.app_context():
            processed = process_pending_jobs()
        if once:
            logger.info("Procesados %s trabajos", processed)
            return
        if not processed:
            time.sleep(app.config["AI_WORKER_POLL_SECONDS"])
