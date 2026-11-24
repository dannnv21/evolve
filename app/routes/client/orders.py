import secrets
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, request

from ...constants import CANCELLABLE_ORDER_STATES, FINAL_ORDER_STATES
from ...db import get_db, placeholders
from ...errors import BadRequest, Conflict, NotFound
from ...http import money, no_content, now, order_code, pagination, paginated, parse_body, parse_query, to_json_text
from ...schemas.common import DateRangeQuery, OptionalReason
from ...schemas.orders import (AvailabilitySlotsQuery, OrderCreateIn, OrderDraftUpdateIn, OrderListQuery, PaymentIn,
                               RatingIn, ReferenceIn, SubmitOrderIn)
from ...security import current_user, roles_required
from ...services import scheduling
from ...services.notifications import notify_staff
from ...services.orders import (add_history, ensure_draft, get_client_order, latest_quote, load_order_rows,
                                modifications_for, order_detail, order_references, paid_amount, serialize_order,
                                serialize_quote, set_modifications)
from ...storage import save_image_upload

bp = Blueprint("orders", __name__, url_prefix="")


@bp.get("/availability")
@roles_required("cliente")
def available_days():
    query = parse_query(DateRangeQuery)
    days, day = [], query.from_
    while day <= query.to:
        free_slots = sum(1 for slot in scheduling.entry_slots(day, current_app.config["ENTRY_SLOT_MINUTES"])
                         if slot["libre"])
        days.append({"fecha": day, "disponible": free_slots > 0, "plazas_libres": free_slots})
        day += timedelta(days=1)
    return jsonify({"data": days})


@bp.get("/availability/slots")
@roles_required("cliente")
def available_slots():
    query = parse_query(AvailabilitySlotsQuery)
    return jsonify({"fecha": query.date_, "duracion_min": query.duracion_min,
                    "franjas": scheduling.entry_slots(query.date_, query.duracion_min)})


@bp.post("/orders")
@roles_required("cliente")
def create_order():
    data = parse_body(OrderCreateIn)
    db = get_db()
    user_id = current_user()["id"]
    if not db.fetch_value("SELECT id FROM vehiculos WHERE id = %s AND propietario_id = %s AND activo = TRUE",
                          (data.vehiculo_id, user_id)):
        raise NotFound("Vehículo no encontrado")
    with db.transaction():
        order_id = db.execute("INSERT INTO pedidos (cliente_id, vehiculo_id, estado) VALUES (%s, %s, 'borrador')",
                              (user_id, data.vehiculo_id))
    return jsonify({"id": order_id, "codigo": order_code(order_id), "estado": "borrador",
                    "vehiculo_id": data.vehiculo_id}), 201


@bp.get("/orders")
@roles_required("cliente")
def list_orders():
    query = parse_query(OrderListQuery)
    page, limit, offset = pagination()
    where, params = "p.cliente_id = %s", [current_user()["id"]]
    finals = placeholders(len(FINAL_ORDER_STATES))
    if query.estado == "en_curso":
        where += f" AND p.estado NOT IN ({finals})"
        params += FINAL_ORDER_STATES
    elif query.estado == "finalizadas":
        where += f" AND p.estado IN ({finals})"
        params += FINAL_ORDER_STATES
    total = get_db().fetch_value(f"SELECT COUNT(*) FROM pedidos p WHERE {where}", params)
    rows = load_order_rows(where, tuple(params), limit=limit, offset=offset)
    modifications = modifications_for([row["id"] for row in rows])
    return paginated([serialize_order(row, modifications[row["id"]]) for row in rows], total, page)


@bp.get("/orders/<int:order_id>")
@roles_required("cliente")
def get_order(order_id: int):
    get_client_order(order_id)
    return jsonify(order_detail(order_id))


@bp.patch("/orders/<int:order_id>")
@roles_required("cliente")
def update_draft(order_id: int):
    data = parse_body(OrderDraftUpdateIn)
    db = get_db()
    with db.transaction():
        order = get_client_order(order_id, for_update=True)
        ensure_draft(order)
        if data.modificaciones is not None:
            set_modifications(order_id, data.modificaciones)
        fields = {}
        if data.descripcion_cliente is not None:
            fields["descripcion_cliente"] = data.descripcion_cliente
        if data.especificaciones is not None:
            fields["especificaciones"] = to_json_text(data.especificaciones)
        if data.fecha_preferida is not None:
            fields["fecha_preferida"] = data.fecha_preferida
        if fields:
            assignments = ", ".join(f"{column} = %s" for column in fields)
            db.execute(f"UPDATE pedidos SET {assignments} WHERE id = %s", (*fields.values(), order_id))
    detail = order_detail(order_id)
    return jsonify({"id": order_id, "estado": detail["estado"], "modificaciones": len(detail["modificaciones"]),
                    "fecha_preferida": detail["fecha_preferida"]})


@bp.post("/orders/<int:order_id>/references")
@roles_required("cliente")
def add_reference(order_id: int):
    data = ReferenceIn.model_validate({key: value for key, value in request.form.items() if value != ""})
    upload = request.files.get("archivo")
    if (upload is None or not upload.filename) and data.pieza_id is None:
        raise BadRequest("Envía una foto o un pieza_id", fields={"archivo": "Obligatorio si no hay pieza_id"})
    db = get_db()
    user_id = current_user()["id"]
    with db.transaction():
        ensure_draft(get_client_order(order_id, for_update=True))
        type_id = db.fetch_value("SELECT id FROM tipos_modificacion WHERE codigo = %s", (data.tipo_modificacion,))
        if type_id is None:
            raise BadRequest("Tipo de modificación no válido", fields={"tipo_modificacion": "No existe"})
        if data.pieza_id and not db.fetch_value("SELECT id FROM piezas WHERE id = %s AND activo = TRUE", (data.pieza_id,)):
            raise NotFound("Pieza no encontrada")
        file_id = (save_image_upload(upload, f"pedidos/{order_id}/referencias", user_id)
                   if upload is not None and upload.filename else None)
        reference_id = db.execute(
            """INSERT INTO referencias_pieza (pedido_id, tipo_modificacion_id, archivo_id, pieza_id, marca, modelo, notas)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (order_id, type_id, file_id, data.pieza_id, data.marca, data.modelo, data.notas))
    reference = next(item for item in order_references(order_id) if item["id"] == reference_id)
    return jsonify(reference), 201


@bp.delete("/orders/<int:order_id>/references/<int:reference_id>")
@roles_required("cliente")
def delete_reference(order_id: int, reference_id: int):
    db = get_db()
    with db.transaction():
        ensure_draft(get_client_order(order_id, for_update=True))
        if not db.fetch_value("SELECT id FROM referencias_pieza WHERE id = %s AND pedido_id = %s",
                              (reference_id, order_id)):
            raise NotFound("Referencia no encontrada")
        db.execute("UPDATE version_entradas SET referencia_id = NULL WHERE referencia_id = %s", (reference_id,))
        db.execute("DELETE FROM referencias_pieza WHERE id = %s", (reference_id,))
    return no_content()


@bp.post("/orders/<int:order_id>/submit")
@roles_required("cliente")
def submit_order(order_id: int):
    data = parse_body(SubmitOrderIn)
    db = get_db()
    with db.transaction():
        order = get_client_order(order_id, for_update=True)
        ensure_draft(order)
        if not db.fetch_value("SELECT COUNT(*) FROM pedido_modificaciones WHERE pedido_id = %s", (order_id,)):
            raise BadRequest("Añade al menos una modificación antes de enviar")
        if data.version_aprobada_id is not None:
            version_state = db.fetch_value("SELECT estado FROM versiones_diseno WHERE id = %s AND pedido_id = %s",
                                           (data.version_aprobada_id, order_id))
            if version_state is None:
                raise NotFound("La versión no pertenece a este pedido")
            if version_state != "listo":
                raise Conflict("Esa versión del diseño aún no está lista", "DESIGN_NOT_READY")

        entry = None
        fields = {"estado": "solicitado", "enviado_en": now(), "version_aprobada_id": data.version_aprobada_id}
        if data.franja_entrada is not None:
            slot = data.franja_entrada
            if slot.inicio <= now() or not scheduling.within_opening_hours(slot.inicio, slot.fin):
                raise BadRequest("La franja debe ser futura y estar dentro del horario del taller")
            employee_id = scheduling.find_free_receptionist(slot.inicio, slot.fin)
            if employee_id is None:
                raise Conflict(f"La franja de {slot.inicio:%H:%M} a {slot.fin:%H:%M} ya está ocupada. Elige otra.",
                               "SLOT_TAKEN")
            vehicle = db.fetch_one("SELECT marca, modelo FROM vehiculos WHERE id = %s", (order["vehiculo_id"],))
            entry_id = db.execute(
                """INSERT INTO citas (tipo, pedido_id, empleado_id, titulo, descripcion, inicio, fin, creado_por)
                   VALUES ('entrada', %s, %s, %s, 'Entrada reservada por el cliente', %s, %s, %s)""",
                (order_id, employee_id, f"Entrada {vehicle['marca']} {vehicle['modelo']}"[:80],
                 slot.inicio, slot.fin, current_user()["id"]))
            fields["fecha_preferida"] = slot.inicio.date()
            entry = {"id": entry_id, "tipo": "entrada", "inicio": slot.inicio, "fin": slot.fin}

        assignments = ", ".join(f"{column} = %s" for column in fields)
        db.execute(f"UPDATE pedidos SET {assignments} WHERE id = %s", (*fields.values(), order_id))
        add_history(order_id, "borrador", "solicitado", "Solicitud recibida", 0)
        notify_staff("solicitud_nueva", "Nueva solicitud de presupuesto",
                     f"{current_user()['nombre']} {current_user()['apellidos']} · {order_code(order_id)}",
                     order_id=order_id)
    return jsonify({"id": order_id, "codigo": order_code(order_id), "estado": "solicitado", "cita_entrada": entry})


@bp.get("/orders/<int:order_id>/timeline")
@roles_required("cliente")
def order_timeline(order_id: int):
    get_client_order(order_id)
    rows = get_db().fetch_all(
        """SELECT estado_nuevo AS estado, progreso, nota, creado_en AS fecha FROM pedido_historial
            WHERE pedido_id = %s AND visible_cliente = TRUE ORDER BY creado_en, id""", (order_id,))
    return jsonify({"data": rows})


@bp.post("/orders/<int:order_id>/cancel")
@roles_required("cliente")
def cancel_order(order_id: int):
    data = parse_body(OptionalReason)
    db = get_db()
    with db.transaction():
        order = get_client_order(order_id, for_update=True)
        if order["estado"] not in CANCELLABLE_ORDER_STATES:
            raise Conflict("No se puede cancelar un pedido que ya está en el taller", "ORDER_NOT_CANCELLABLE")
        released = db.execute_rowcount(
            "UPDATE citas SET estado = 'cancelada' WHERE pedido_id = %s AND estado = 'programada'", (order_id,))
        db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = %s", (order_id,))
        add_history(order_id, order["estado"], "cancelado", data.motivo or "Cancelado por el cliente")
        if order["estado"] != "borrador":
            notify_staff("estado_pedido", f"Pedido {order_code(order_id)} cancelado por el cliente", data.motivo,
                         order_id=order_id)
    return jsonify({"id": order_id, "estado": "cancelado", "citas_liberadas": released})


@bp.get("/orders/<int:order_id>/quote")
@roles_required("cliente")
def get_quote(order_id: int):
    get_client_order(order_id)
    quote = latest_quote(order_id)
    if quote is None:
        raise NotFound("Este pedido aún no tiene presupuesto")
    return jsonify(serialize_quote(quote))


def _answer_quote(order_id: int, accepted: bool, reason=None):
    db = get_db()
    with db.transaction():
        order = get_client_order(order_id, for_update=True)
        quote = latest_quote(order_id)
        if quote is None:
            raise NotFound("Este pedido aún no tiene presupuesto")
        if quote["estado"] != "enviado":
            raise Conflict("Este presupuesto ya se aceptó o rechazó", "QUOTE_ALREADY_ANSWERED")
        quote_state = "aceptado" if accepted else "rechazado"
        order_state = "aceptado" if accepted else "solicitado"
        db.execute("UPDATE presupuestos SET estado = %s, respondido_en = %s, motivo_rechazo = %s WHERE id = %s",
                   (quote_state, now(), reason, quote["id"]))
        db.execute("UPDATE pedidos SET estado = %s WHERE id = %s", (order_state, order_id))
        add_history(order_id, order["estado"], order_state,
                    "Presupuesto aceptado por el cliente" if accepted else f"Presupuesto rechazado: {reason or 'sin motivo'}")
        notify_staff("presupuesto", f"Presupuesto {quote_state} · {order_code(order_id)}", reason, order_id=order_id)
    return jsonify({"pedido_id": order_id, "estado": order_state,
                    "presupuesto": {"id": quote["id"], "estado": quote_state}})


@bp.post("/orders/<int:order_id>/quote/accept")
@roles_required("cliente")
def accept_quote(order_id: int):
    return _answer_quote(order_id, accepted=True)


@bp.post("/orders/<int:order_id>/quote/reject")
@roles_required("cliente")
def reject_quote(order_id: int):
    return _answer_quote(order_id, accepted=False, reason=parse_body(OptionalReason).motivo)


def _accepted_quote(order_id: int) -> dict:
    quote = get_db().fetch_one(
        "SELECT * FROM presupuestos WHERE pedido_id = %s AND estado = 'aceptado' ORDER BY numero DESC LIMIT 1",
        (order_id,))
    if quote is None:
        raise Conflict("El pedido no tiene un presupuesto aceptado", "NO_ACCEPTED_QUOTE")
    return quote


@bp.post("/orders/<int:order_id>/payment")
@roles_required("cliente")
def pay_order(order_id: int):
    data = parse_body(PaymentIn)
    db = get_db()
    with db.transaction():
        get_client_order(order_id, for_update=True)
        quote = _accepted_quote(order_id)
        pending = money(quote["total"]) - paid_amount(order_id)
        if pending <= 0:
            raise Conflict("Este pedido ya está pagado", "ALREADY_PAID")
        if money(data.importe) > pending:
            raise BadRequest(f"El importe supera lo pendiente ({pending} €)", "INVALID_AMOUNT")
        reference = "SIM-" + secrets.token_hex(3).upper()
        paid_at = now()
        payment_id = db.execute(
            """INSERT INTO pagos (concepto, pedido_id, presupuesto_id, metodo, importe, estado, referencia, pagado_en)
               VALUES ('pedido', %s, %s, %s, %s, 'completado', %s, %s)""",
            (order_id, quote["id"], data.metodo, money(data.importe), reference, paid_at))
    return jsonify({"id": payment_id, "estado": "completado", "importe": money(data.importe),
                    "referencia": reference, "pagado_en": paid_at}), 201


@bp.get("/orders/<int:order_id>/payment")
@roles_required("cliente")
def payment_status(order_id: int):
    get_client_order(order_id)
    quote = latest_quote(order_id)
    total = money(quote["total"]) if quote and quote["estado"] == "aceptado" else money(0)
    paid = paid_amount(order_id)
    payments = get_db().fetch_all(
        """SELECT id, metodo, importe, estado, referencia, pagado_en FROM pagos
            WHERE pedido_id = %s AND concepto = 'pedido' ORDER BY id""", (order_id,))
    return jsonify({"total": total, "pagado": paid, "pendiente": max(total - paid, money(0)),
                    "pagos": [{**row, "importe": money(row["importe"])} for row in payments]})


@bp.post("/orders/<int:order_id>/rating")
@roles_required("cliente")
def rate_order(order_id: int):
    data = parse_body(RatingIn)
    db = get_db()
    with db.transaction():
        order = get_client_order(order_id, for_update=True)
        if order["estado"] != "entregado":
            raise Conflict("Solo se puede valorar un pedido entregado", "ORDER_NOT_DELIVERED")
        if db.fetch_value("SELECT id FROM valoraciones WHERE pedido_id = %s", (order_id,)):
            raise Conflict("Ya valoraste este pedido", "ALREADY_RATED")
        rating_id = db.execute(
            "INSERT INTO valoraciones (pedido_id, cliente_id, puntuacion, comentario) VALUES (%s, %s, %s, %s)",
            (order_id, current_user()["id"], data.puntuacion, data.comentario))
    return jsonify({"id": rating_id, "pedido_id": order_id, "puntuacion": data.puntuacion}), 201


@bp.get("/orders/<int:order_id>/rating")
@roles_required("cliente")
def get_rating(order_id: int):
    get_client_order(order_id)
    rating = get_db().fetch_one(
        "SELECT id, pedido_id, puntuacion, comentario, creado_en FROM valoraciones WHERE pedido_id = %s", (order_id,))
    if rating is None:
        raise NotFound("Este pedido no tiene valoración")
    return jsonify(rating)
