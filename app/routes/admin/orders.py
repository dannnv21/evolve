from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, current_app, jsonify, request

from ...constants import REPLICA_TRANSITIONS
from ...db import get_db
from ...errors import BadRequest, Conflict, NotFound
from ...http import as_datetime, money, no_content, now, order_code, pagination, paginated, parse_body, parse_query
from ...schemas.common import RequiredReason
from ...schemas.orders import (AdminOrderCreateIn, AdminOrderListQuery, AdminOrderUpdateIn, AssigneeIn,
                               AssignPartIn, OrderStatusIn, QuoteIn, ReplicaStatusIn)
from ...schemas.workshop import changed_fields
from ...security import current_employee_id, current_user, is_restricted_employee, staff_permission
from ...services.designs import serialize_version
from ...services.notifications import notify, notify_order_client
from ...services.orders import (add_history, change_status, default_quote_expiry, get_staff_order, latest_quote,
                                load_order_rows, modifications_for, order_detail, quote_totals, serialize_order,
                                serialize_quote, set_modifications)
from ...services.stock import move_stock
from ...storage import signed_url

bp = Blueprint("admin_orders", __name__, url_prefix="/admin")


@bp.get("/orders/summary")
@staff_permission("pedidos")
def orders_summary():
    db = get_db()
    today_start = datetime.combine(datetime.now().date(), datetime.min.time())
    return jsonify({
        "solicitudes_nuevas": db.fetch_value("SELECT COUNT(*) FROM pedidos WHERE estado = 'solicitado'"),
        "en_produccion": db.fetch_value("SELECT COUNT(*) FROM pedidos WHERE estado IN ('en_proceso', 'esperando_piezas')"),
        "entregas_hoy": db.fetch_value(
            "SELECT COUNT(*) FROM pedidos WHERE entrega_estimada >= %s AND entrega_estimada < %s AND estado <> 'entregado'",
            (today_start, today_start + timedelta(days=1))),
        "presupuestos_pendientes": db.fetch_value("SELECT COUNT(*) FROM presupuestos WHERE estado = 'enviado'"),
    })


@bp.get("/orders")
@staff_permission("pedidos")
def list_orders():
    query = parse_query(AdminOrderListQuery)
    page, limit, offset = pagination()
    conditions, params = ["p.estado <> 'borrador'"], []
    if query.estado:
        conditions.append("p.estado = %s")
        params.append(query.estado)
    if query.cliente:
        pattern = f"%{query.cliente.lower()}%"
        conditions.append("(LOWER(c.nombre) LIKE %s OR LOWER(c.apellidos) LIKE %s OR LOWER(c.email) LIKE %s "
                          "OR LOWER(v.matricula) LIKE %s)")
        params += [pattern] * 4
    if query.fecha:
        day_start = datetime.combine(query.fecha, datetime.min.time())
        conditions.append("(p.fecha_preferida = %s OR EXISTS (SELECT 1 FROM citas ci WHERE ci.pedido_id = p.id "
                          "AND ci.estado <> 'cancelada' AND ci.inicio >= %s AND ci.inicio < %s))")
        params += [query.fecha, day_start, day_start + timedelta(days=1)]
    if query.responsable_id:
        conditions.append("p.responsable_id = %s")
        params.append(query.responsable_id)
    if is_restricted_employee():
        conditions.append("p.responsable_id = %s")
        params.append(current_employee_id() or 0)
    where = " AND ".join(conditions)
    total = get_db().fetch_value(
        f"""SELECT COUNT(*) FROM pedidos p JOIN usuarios c ON c.id = p.cliente_id
              JOIN vehiculos v ON v.id = p.vehiculo_id WHERE {where}""", params)
    rows = load_order_rows(where, tuple(params), limit=limit, offset=offset)
    modifications = modifications_for([row["id"] for row in rows])
    data = []
    for row in rows:
        order = serialize_order(row, modifications[row["id"]])
        order["cliente"] = {"id": row["cliente_id"], "nombre": f"{row['cliente_nombre']} {row['cliente_apellidos']}"}
        order["servicio"] = " + ".join(item["nombre"] for item in modifications[row["id"]])
        data.append(order)
    return paginated(data, total, page)


@bp.post("/orders")
@staff_permission("pedidos", write=True)
def create_order():
    data = parse_body(AdminOrderCreateIn)
    db = get_db()
    with db.transaction():
        if not db.fetch_value(
                "SELECT u.id FROM usuarios u JOIN roles r ON r.id = u.rol_id WHERE u.id = %s AND r.codigo = 'cliente'",
                (data.cliente_id,)):
            raise NotFound("Cliente no encontrado")
        if not db.fetch_value("SELECT id FROM vehiculos WHERE id = %s AND propietario_id = %s AND activo = TRUE",
                              (data.vehiculo_id, data.cliente_id)):
            raise BadRequest("El vehículo no pertenece a ese cliente", fields={"vehiculo_id": "No pertenece al cliente"})
        order_id = db.execute(
            """INSERT INTO pedidos (cliente_id, vehiculo_id, estado, descripcion_cliente, entrega_estimada, enviado_en)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (data.cliente_id, data.vehiculo_id, data.estado, data.descripcion_cliente, data.entrega_estimada, now()))
        set_modifications(order_id, data.modificaciones)
        add_history(order_id, None, data.estado, "Pedido registrado en recepción")
    return jsonify({"id": order_id, "codigo": order_code(order_id), "estado": data.estado}), 201


@bp.get("/orders/<int:order_id>")
@staff_permission("pedidos")
def get_order(order_id: int):
    order = get_staff_order(order_id)
    db = get_db()
    detail = order_detail(order_id)
    client = db.fetch_one("SELECT id, nombre, apellidos, email, telefono FROM usuarios WHERE id = %s",
                          (order["cliente_id"],))
    detail["cliente"] = {"id": client["id"], "nombre": f"{client['nombre']} {client['apellidos']}",
                         "email": client["email"], "telefono": client["telefono"]}
    photos = db.fetch_all("SELECT angulo, archivo_id FROM fotos_vehiculo WHERE vehiculo_id = %s AND activa = TRUE",
                          (order["vehiculo_id"],))
    detail["fotos_vehiculo"] = [{"angulo": photo["angulo"], "url": signed_url(photo["archivo_id"])} for photo in photos]
    approved_version_id = order["version_aprobada_id"] or order["version_actual_id"]
    if approved_version_id:
        version = db.fetch_one("SELECT * FROM versiones_diseno WHERE id = %s", (approved_version_id,))
        detail["diseno"] = serialize_version(version, with_frames=True) if version else None
    quotes = db.fetch_all("SELECT * FROM presupuestos WHERE pedido_id = %s ORDER BY numero", (order_id,))
    detail["presupuestos"] = [serialize_quote(quote) for quote in quotes]
    detail["piezas"] = [{**row, "precio_unitario": money(row["precio_unitario"])} for row in db.fetch_all(
        """SELECT pp.pieza_id, pz.referencia, pz.nombre, pp.cantidad, pp.precio_unitario
             FROM pedido_piezas pp JOIN piezas pz ON pz.id = pp.pieza_id WHERE pp.pedido_id = %s""", (order_id,))]
    return jsonify(detail)


@bp.patch("/orders/<int:order_id>")
@staff_permission("pedidos", write=True)
def update_order(order_id: int):
    changes = changed_fields(parse_body(AdminOrderUpdateIn))
    db = get_db()
    with db.transaction():
        order = get_staff_order(order_id, for_update=True)
        entry = as_datetime(changes.get("fecha_entrada", order["fecha_entrada"]))
        for field in ("entrega_estimada", "entrega_real"):
            delivery = as_datetime(changes.get(field, order[field]))
            if entry and delivery and delivery < entry:
                raise BadRequest("La entrega no puede ser anterior a la entrada", fields={field: "Anterior a la entrada"})
        if changes:
            assignments = ", ".join(f"{column} = %s" for column in changes)
            db.execute(f"UPDATE pedidos SET {assignments} WHERE id = %s", (*changes.values(), order_id))
    updated = db.fetch_one("SELECT id, fecha_entrada, entrega_estimada, entrega_real, descripcion_cliente "
                           "FROM pedidos WHERE id = %s", (order_id,))
    return jsonify(updated)


@bp.post("/orders/<int:order_id>/accept")
@staff_permission("pedidos", write=True)
def accept_request(order_id: int):
    db = get_db()
    with db.transaction():
        order = get_staff_order(order_id, for_update=True)
        if order["estado"] != "solicitado":
            raise Conflict("Solo se aceptan solicitudes en estado solicitado", "INVALID_TRANSITION")
        if order["responsable_id"] is None and current_employee_id():
            db.execute("UPDATE pedidos SET responsable_id = %s WHERE id = %s", (current_employee_id(), order_id))
        add_history(order_id, "solicitado", "solicitado", "La modificación es viable. Preparando presupuesto.")
        notify_order_client(order, f"Hemos revisado tu solicitud {order_code(order_id)}",
                            "Es viable: en breve recibirás el presupuesto")
    return jsonify({"id": order_id, "estado": "solicitado", "aceptada_por_taller": True})


@bp.post("/orders/<int:order_id>/reject")
@staff_permission("pedidos", write=True)
def reject_request(order_id: int):
    data = parse_body(RequiredReason)
    db = get_db()
    with db.transaction():
        order = get_staff_order(order_id, for_update=True)
        if order["estado"] not in ("solicitado", "presupuestado"):
            raise Conflict("Solo se rechazan solicitudes que aún no se han aceptado", "INVALID_TRANSITION")
        db.execute("UPDATE pedidos SET estado = 'rechazado', motivo_rechazo_taller = %s WHERE id = %s",
                   (data.motivo, order_id))
        db.execute("UPDATE presupuestos SET estado = 'caducado' WHERE pedido_id = %s AND estado IN ('borrador', 'enviado')",
                   (order_id,))
        db.execute("UPDATE citas SET estado = 'cancelada' WHERE pedido_id = %s AND estado = 'programada'", (order_id,))
        add_history(order_id, order["estado"], "rechazado", data.motivo)
        notify_order_client(order, f"No podemos realizar tu pedido {order_code(order_id)}", data.motivo)
    return jsonify({"id": order_id, "estado": "rechazado", "motivo_rechazo_taller": data.motivo})


@bp.patch("/orders/<int:order_id>/status")
@staff_permission("pedidos", write=True)
def update_status(order_id: int):
    data = parse_body(OrderStatusIn)
    db = get_db()
    with db.transaction():
        order = get_staff_order(order_id, for_update=True)
        result = change_status(order, data.estado, data.progreso, data.nota, data.visible_cliente)
    return jsonify(result)


@bp.put("/orders/<int:order_id>/assignee")
@staff_permission("pedidos", write=True)
def assign_employee(order_id: int):
    data = parse_body(AssigneeIn)
    db = get_db()
    with db.transaction():
        get_staff_order(order_id, for_update=True)
        employee = db.fetch_one(
            """SELECT e.id, e.disponibilidad, e.ausente_hasta, u.nombre, u.apellidos FROM empleados e
                 JOIN usuarios u ON u.id = e.usuario_id WHERE e.id = %s AND e.fecha_baja IS NULL""",
            (data.empleado_id,))
        if employee is None:
            raise NotFound("Empleado no encontrado")
        full_name = f"{employee['nombre']} {employee['apellidos']}"
        if employee["disponibilidad"] != "disponible":
            until = f" hasta el {employee['ausente_hasta']:%d/%m}" if employee["ausente_hasta"] else ""
            raise Conflict(f"{full_name} no está disponible{until}", "EMPLOYEE_UNAVAILABLE")
        db.execute("UPDATE pedidos SET responsable_id = %s WHERE id = %s", (data.empleado_id, order_id))
        add_history(order_id, None, get_staff_order(order_id)["estado"], f"Responsable: {full_name}",
                    visible_to_client=False)
    return jsonify({"id": order_id, "responsable": {"empleado_id": data.empleado_id, "nombre": full_name}})


@bp.put("/orders/<int:order_id>/quote")
@staff_permission("pedidos", write=True)
def save_quote(order_id: int):
    data = parse_body(QuoteIn)
    db = get_db()
    with db.transaction():
        order = get_staff_order(order_id, for_update=True)
        if order["estado"] != "solicitado":
            raise Conflict("Solo se presupuesta un pedido en estado solicitado", "INVALID_STATE")
        lines = _resolve_quote_lines(data.lineas)
        vat = money(data.iva_pct if data.iva_pct is not None else current_app.config["VAT_PERCENT"])
        base, total = quote_totals(lines, vat)
        draft = latest_quote(order_id, include_drafts=True)
        if draft and draft["estado"] == "borrador":
            quote_id = draft["id"]
            db.execute("DELETE FROM presupuesto_lineas WHERE presupuesto_id = %s", (quote_id,))
            db.execute("UPDATE presupuestos SET base_imponible = %s, iva_pct = %s, total = %s, valido_hasta = %s "
                       "WHERE id = %s", (base, vat, total, data.valido_hasta, quote_id))
        else:
            number = (draft["numero"] + 1) if draft else 1
            quote_id = db.execute(
                """INSERT INTO presupuestos (pedido_id, numero, estado, base_imponible, iva_pct, total, valido_hasta,
                                             creado_por)
                   VALUES (%s, %s, 'borrador', %s, %s, %s, %s, %s)""",
                (order_id, number, base, vat, total, data.valido_hasta, current_user()["id"]))
        for line in lines:
            db.execute(
                """INSERT INTO presupuesto_lineas (presupuesto_id, tipo, servicio_id, pieza_id, descripcion, cantidad,
                                                   precio_unitario)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (quote_id, line["tipo"], line["servicio_id"], line["pieza_id"], line["descripcion"],
                 line["cantidad"], line["precio_unitario"]))
    quote = db.fetch_one("SELECT * FROM presupuestos WHERE id = %s", (quote_id,))
    return jsonify(serialize_quote(quote))


def _resolve_quote_lines(lines) -> list[dict]:
    """Completa descripción y precio desde el catálogo cuando la línea no los trae."""
    db = get_db()
    resolved = []
    for index, line in enumerate(lines):
        description, unit_price = line.descripcion, line.precio_unitario
        if line.tipo == "servicio":
            service = db.fetch_one("SELECT nombre, precio_base FROM servicios WHERE id = %s", (line.servicio_id,))
            if service is None:
                raise BadRequest("Servicio no encontrado", fields={f"lineas[{index}].servicio_id": "No existe"})
            description = description or service["nombre"]
            unit_price = unit_price if unit_price is not None else service["precio_base"]
        elif line.tipo == "pieza":
            part = db.fetch_one("SELECT nombre, precio_venta FROM piezas WHERE id = %s", (line.pieza_id,))
            if part is None:
                raise BadRequest("Pieza no encontrada", fields={f"lineas[{index}].pieza_id": "No existe"})
            description = description or part["nombre"]
            unit_price = unit_price if unit_price is not None else part["precio_venta"]
        resolved.append({"tipo": line.tipo, "servicio_id": line.servicio_id, "pieza_id": line.pieza_id,
                         "descripcion": description[:120], "cantidad": Decimal(line.cantidad),
                         "precio_unitario": money(unit_price)})
    return resolved


@bp.post("/orders/<int:order_id>/quote/send")
@staff_permission("pedidos", write=True)
def send_quote(order_id: int):
    db = get_db()
    with db.transaction():
        order = get_staff_order(order_id, for_update=True)
        draft = latest_quote(order_id, include_drafts=True)
        if draft is None or draft["estado"] != "borrador":
            raise NotFound("No hay ningún presupuesto en borrador para enviar")
        if not db.fetch_value("SELECT COUNT(*) FROM presupuesto_lineas WHERE presupuesto_id = %s", (draft["id"],)):
            raise Conflict("El presupuesto no tiene líneas", "QUOTE_EMPTY")
        db.execute("UPDATE presupuestos SET estado = 'enviado', enviado_en = %s, valido_hasta = %s WHERE id = %s",
                   (now(), draft["valido_hasta"] or default_quote_expiry(), draft["id"]))
        db.execute("UPDATE pedidos SET estado = 'presupuestado' WHERE id = %s", (order_id,))
        add_history(order_id, order["estado"], "presupuestado", "Presupuesto enviado")
        notify_order_client(order, "Tienes un presupuesto", f"Total: {money(draft['total'])} €", "presupuesto")
    return jsonify({"pedido_id": order_id, "estado": "presupuestado",
                    "presupuesto": {"id": draft["id"], "estado": "enviado", "total": money(draft["total"])}})


@bp.post("/orders/<int:order_id>/parts")
@staff_permission("pedidos", write=True)
def assign_part(order_id: int):
    data = parse_body(AssignPartIn)
    db = get_db()
    with db.transaction():
        get_staff_order(order_id, for_update=True)
        part = db.fetch_one("SELECT id, precio_venta, activo FROM piezas WHERE id = %s", (data.pieza_id,))
        if part is None or not part["activo"]:
            raise NotFound("Pieza no encontrada")
        remaining = move_stock(data.pieza_id, -data.cantidad, "salida_pedido", current_user()["id"],
                               order_id=order_id, reason=f"Asignada a {order_code(order_id)}")
        assigned = db.fetch_value("SELECT cantidad FROM pedido_piezas WHERE pedido_id = %s AND pieza_id = %s",
                                  (order_id, data.pieza_id))
        if assigned:
            db.execute("UPDATE pedido_piezas SET cantidad = cantidad + %s WHERE pedido_id = %s AND pieza_id = %s",
                       (data.cantidad, order_id, data.pieza_id))
        else:
            db.execute("INSERT INTO pedido_piezas (pedido_id, pieza_id, cantidad, precio_unitario) VALUES (%s, %s, %s, %s)",
                       (order_id, data.pieza_id, data.cantidad, part["precio_venta"]))
    return jsonify({"pedido_id": order_id, "pieza_id": data.pieza_id, "cantidad": (assigned or 0) + data.cantidad,
                    "stock_restante": remaining}), 201


@bp.delete("/orders/<int:order_id>/parts/<int:part_id>")
@staff_permission("pedidos", write=True)
def return_part(order_id: int, part_id: int):
    db = get_db()
    with db.transaction():
        get_staff_order(order_id, for_update=True)
        quantity = db.fetch_value("SELECT cantidad FROM pedido_piezas WHERE pedido_id = %s AND pieza_id = %s",
                                  (order_id, part_id))
        if not quantity:
            raise NotFound("Esa pieza no está en el pedido")
        db.execute("DELETE FROM pedido_piezas WHERE pedido_id = %s AND pieza_id = %s", (order_id, part_id))
        move_stock(part_id, quantity, "devolucion", current_user()["id"], order_id=order_id,
                   reason=f"Devuelta de {order_code(order_id)}")
    return no_content()


@bp.get("/customers")
@staff_permission("pedidos")
def search_customers():
    page, limit, offset = pagination()
    term = f"%{(request.args.get('q') or '').lower()}%"
    where = """r.codigo = 'cliente' AND u.eliminado_en IS NULL AND (LOWER(u.nombre) LIKE %s OR LOWER(u.apellidos) LIKE %s
               OR LOWER(u.email) LIKE %s OR u.telefono LIKE %s OR LOWER(COALESCE(u.dni, '')) LIKE %s)"""
    params = (term,) * 5
    db = get_db()
    total = db.fetch_value(f"SELECT COUNT(*) FROM usuarios u JOIN roles r ON r.id = u.rol_id WHERE {where}", params)
    rows = db.fetch_all(
        f"""SELECT u.id, u.nombre, u.apellidos, u.email, u.telefono FROM usuarios u JOIN roles r ON r.id = u.rol_id
             WHERE {where} ORDER BY u.nombre, u.apellidos LIMIT {limit} OFFSET {offset}""", params)
    data = [{"id": row["id"], "nombre": f"{row['nombre']} {row['apellidos']}", "email": row["email"],
             "telefono": row["telefono"]} for row in rows]
    return paginated(data, total, page)


@bp.get("/customers/<int:customer_id>/vehicles")
@staff_permission("pedidos")
def customer_vehicles(customer_id: int):
    db = get_db()
    if not db.fetch_value("SELECT u.id FROM usuarios u JOIN roles r ON r.id = u.rol_id "
                          "WHERE u.id = %s AND r.codigo = 'cliente'", (customer_id,)):
        raise NotFound("Cliente no encontrado")
    rows = db.fetch_all("SELECT id, marca, modelo, variante, anio, matricula, color FROM vehiculos "
                        "WHERE propietario_id = %s AND activo = TRUE ORDER BY marca", (customer_id,))
    return jsonify({"data": rows, "total": len(rows), "page": 1})


@bp.patch("/replicas/<int:replica_id>/status")
@staff_permission("pedidos", write=True)
def update_replica_status(replica_id: int):
    data = parse_body(ReplicaStatusIn)
    db = get_db()
    with db.transaction():
        replica = db.fetch_one("SELECT * FROM replicas WHERE id = %s FOR UPDATE", (replica_id,))
        if replica is None:
            raise NotFound("Réplica no encontrada")
        allowed_from = REPLICA_TRANSITIONS[data.estado]
        if replica["estado"] not in allowed_from:
            reason = ("El cliente aún no ha aprobado el modelo" if data.estado == "imprimiendo"
                      else f"No se puede pasar de {replica['estado']} a {data.estado}")
            raise Conflict(reason, "INVALID_TRANSITION")
        db.execute("UPDATE replicas SET estado = %s, notas = COALESCE(%s, notas) WHERE id = %s",
                   (data.estado, data.notas, replica_id))
        messages = {"imprimiendo": "Estamos imprimiendo tu réplica", "lista": "Tu réplica está lista",
                    "entregada": "Réplica entregada", "cancelada": "Tu réplica se ha cancelado"}
        notify(replica["cliente_id"], "replica", messages[data.estado], data.notas, order_id=replica["pedido_id"],
               replica_id=replica_id)
    return jsonify({"id": replica_id, "estado": data.estado})
