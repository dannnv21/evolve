import secrets

from flask import Blueprint, current_app, jsonify

from ...constants import REPLICA_PRICES
from ...db import get_db
from ...errors import BadRequest, Conflict, NotFound
from ...http import money, no_content, now, parse_body
from ...schemas.orders import PaymentIn, ReplicaIn
from ...security import current_user, roles_required
from ...services.designs import enqueue_job
from ...services.notifications import notify_staff
from ...storage import signed_url

bp = Blueprint("replicas", __name__, url_prefix="/replicas")


def replica_price(delivery: str, scale: str):
    prices = REPLICA_PRICES[delivery]
    return money(prices.get(scale, prices.get("default")))


def with_vat(amount):
    return money(amount * (1 + money(current_app.config["VAT_PERCENT"]) / 100))


def serialize_replica(row: dict) -> dict:
    return {"id": row["id"], "pedido_id": row["pedido_id"], "version_id": row["version_id"],
            "formato_entrega": row["formato_entrega"], "escala": row["escala"], "material": row["material"],
            "acabado": row["acabado"], "estado": row["estado"], "precio": money(row["precio"]),
            "precio_con_iva": with_vat(money(row["precio"])),
            "modelo_glb_url": signed_url(row["modelo_glb_id"]), "modelo_stl_url": signed_url(row["modelo_stl_id"]),
            "preview_url": signed_url(row["preview_id"]), "notas": row["notas"], "creado_en": row["creado_en"]}


def owned_replica(replica_id: int, for_update: bool = False) -> dict:
    sql = "SELECT * FROM replicas WHERE id = %s AND cliente_id = %s" + (" FOR UPDATE" if for_update else "")
    replica = get_db().fetch_one(sql, (replica_id, current_user()["id"]))
    if replica is None:
        raise NotFound("Réplica no encontrada")
    return replica


@bp.get("/options")
def replica_options():
    prices = [{"formato": "archivo_digital", "precio": money(REPLICA_PRICES["archivo_digital"]["default"])}]
    prices += [{"formato": "impresion_taller", "escala": scale, "precio": money(price)}
               for scale, price in REPLICA_PRICES["impresion_taller"].items()]
    return jsonify({"formatos": ["archivo_digital", "impresion_taller"],
                    "escalas": list(REPLICA_PRICES["impresion_taller"]), "materiales": ["PLA", "PETG", "resina"],
                    "acabados": ["sin_pintar", "pintada"], "precios": prices,
                    "iva_pct": current_app.config["VAT_PERCENT"]})


@bp.post("")
@roles_required("cliente")
def request_replica():
    data = parse_body(ReplicaIn)
    db = get_db()
    user_id = current_user()["id"]
    if not db.fetch_value("SELECT id FROM pedidos WHERE id = %s AND cliente_id = %s", (data.pedido_id, user_id)):
        raise NotFound("Pedido no encontrado")
    version_state = db.fetch_value("SELECT estado FROM versiones_diseno WHERE id = %s AND pedido_id = %s",
                                   (data.version_id, data.pedido_id))
    if version_state is None:
        raise NotFound("Versión de diseño no encontrada")
    if version_state != "listo":
        raise Conflict("El diseño aún no está terminado", "DESIGN_NOT_READY")
    price = replica_price(data.formato_entrega, data.escala)
    with db.transaction():
        replica_id = db.execute(
            """INSERT INTO replicas (pedido_id, version_id, cliente_id, formato_entrega, escala, material, acabado,
                                     estado, precio)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'generando_modelo', %s)""",
            (data.pedido_id, data.version_id, user_id, data.formato_entrega, data.escala,
             data.material if data.formato_entrega == "impresion_taller" else None,
             data.acabado if data.formato_entrega == "impresion_taller" else None, price))
        job_id = enqueue_job("modelo_3d", user_id, version_id=data.version_id, replica_id=replica_id)
    return jsonify({"id": replica_id, "estado": "generando_modelo", "job_id": job_id, "precio": price}), 202


@bp.get("")
@roles_required("cliente")
def list_replicas():
    rows = get_db().fetch_all("SELECT * FROM replicas WHERE cliente_id = %s ORDER BY creado_en DESC",
                              (current_user()["id"],))
    return jsonify({"data": [serialize_replica(row) for row in rows], "total": len(rows), "page": 1})


@bp.get("/<int:replica_id>")
@roles_required("cliente")
def get_replica(replica_id: int):
    return jsonify(serialize_replica(owned_replica(replica_id)))


@bp.post("/<int:replica_id>/approve")
@roles_required("cliente")
def approve_replica(replica_id: int):
    db = get_db()
    with db.transaction():
        replica = owned_replica(replica_id, for_update=True)
        if replica["estado"] != "modelo_listo":
            raise Conflict("El modelo 3D aún no está listo para aprobar", "REPLICA_NOT_READY")
        db.execute("UPDATE replicas SET estado = 'aprobada' WHERE id = %s", (replica_id,))
        if replica["formato_entrega"] == "impresion_taller":
            notify_staff("replica", "Réplica aprobada, lista para imprimir", f"Réplica {replica_id} · {replica['escala']}",
                         replica_id=replica_id)
    return jsonify({"id": replica_id, "estado": "aprobada"})


@bp.post("/<int:replica_id>/payment")
@roles_required("cliente")
def pay_replica(replica_id: int):
    data = parse_body(PaymentIn)
    db = get_db()
    with db.transaction():
        replica = owned_replica(replica_id, for_update=True)
        if replica["estado"] in ("cancelada", "error"):
            raise Conflict("No se puede pagar una réplica cancelada", "REPLICA_CANCELLED")
        if db.fetch_value("SELECT id FROM pagos WHERE replica_id = %s AND estado = 'completado'", (replica_id,)):
            raise Conflict("Esta réplica ya está pagada", "ALREADY_PAID")
        amount_due = with_vat(money(replica["precio"]))
        if money(data.importe) != amount_due:
            raise BadRequest(f"El importe debe ser {amount_due} €", "INVALID_AMOUNT")
        payment_id = db.execute(
            """INSERT INTO pagos (concepto, pedido_id, replica_id, metodo, importe, estado, referencia, pagado_en)
               VALUES ('replica', %s, %s, %s, %s, 'completado', %s, %s)""",
            (replica["pedido_id"], replica_id, data.metodo, amount_due,
             "SIM-" + secrets.token_hex(3).upper(), now()))
    return jsonify({"id": payment_id, "concepto": "replica", "estado": "completado", "importe": amount_due}), 201


@bp.delete("/<int:replica_id>")
@roles_required("cliente")
def cancel_replica(replica_id: int):
    db = get_db()
    with db.transaction():
        replica = owned_replica(replica_id, for_update=True)
        if replica["estado"] not in ("solicitada", "generando_modelo", "modelo_listo", "aprobada", "error"):
            raise Conflict("No se puede cancelar: ya se está imprimiendo o se ha entregado", "REPLICA_NOT_CANCELLABLE")
        db.execute("UPDATE replicas SET estado = 'cancelada' WHERE id = %s", (replica_id,))
        db.execute("UPDATE trabajos_ia SET estado = 'cancelado' WHERE replica_id = %s AND estado = 'en_cola'",
                   (replica_id,))
    return no_content()
