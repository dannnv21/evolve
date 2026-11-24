from flask import Blueprint, jsonify, request

from ...db import get_db, placeholders
from ...errors import BadRequest, Conflict, NotFound
from ...http import money, no_content, now, pagination, paginated, parse_body, parse_query, purchase_code
from ...schemas.workshop import (PartIn, PartListQuery, PartUpdateIn, PurchaseIn, PurchaseUpdateIn,
                                 ReceivePurchaseIn, StockAdjustmentIn, SupplierIn, changed_fields)
from ...security import current_user, staff_permission
from ...services.stock import STOCK_STATUS_SQL, move_stock

bp = Blueprint("admin_warehouse", __name__, url_prefix="/admin")

PART_SELECT = f"""
    SELECT p.id, p.referencia, p.nombre, p.descripcion, p.marca, p.categoria_id, c.nombre AS categoria,
           p.proveedor_id, pr.nombre AS proveedor, p.precio_venta, p.coste, p.stock, p.stock_minimo, p.ubicacion,
           p.activo, p.actualizado_en, {STOCK_STATUS_SQL} AS estado_stock
      FROM piezas p
      JOIN categorias_pieza c ON c.id = p.categoria_id
      LEFT JOIN proveedores pr ON pr.id = p.proveedor_id
"""


def serialize_part(row: dict, detail: bool = False) -> dict:
    part = {**row, "precio_venta": money(row["precio_venta"]),
            "coste": money(row["coste"]) if row["coste"] is not None else None, "activo": bool(row["activo"])}
    if detail:
        target = max(row["stock_minimo"] * 2, 1)
        part["diferencia"] = row["stock"] - row["stock_minimo"]
        part["nivel_disponibilidad_pct"] = min(100, round(100 * row["stock"] / target))
    return part


def _part(part_id: int) -> dict:
    row = get_db().fetch_one(f"{PART_SELECT} WHERE p.id = %s AND p.activo = TRUE", (part_id,))
    if row is None:
        raise NotFound("Pieza no encontrada")
    return row


def _validate_part_links(category_id=None, supplier_id=None) -> None:
    db = get_db()
    if category_id and not db.fetch_value("SELECT id FROM categorias_pieza WHERE id = %s", (category_id,)):
        raise BadRequest("Categoría no válida", fields={"categoria_id": "No existe"})
    if supplier_id and not db.fetch_value("SELECT id FROM proveedores WHERE id = %s AND activo = TRUE", (supplier_id,)):
        raise BadRequest("Proveedor no válido", fields={"proveedor_id": "No existe"})


@bp.get("/warehouse/summary")
@staff_permission("almacen")
def warehouse_summary():
    db = get_db()
    return jsonify({
        "referencias": db.fetch_value("SELECT COUNT(*) FROM piezas WHERE activo = TRUE"),
        "stock_bajo": db.fetch_value("SELECT COUNT(*) FROM piezas WHERE activo = TRUE AND stock <= stock_minimo * 2"),
        "stock_critico": db.fetch_value("SELECT COUNT(*) FROM piezas WHERE activo = TRUE AND stock <= stock_minimo"),
        "pedidos_en_camino": db.fetch_value("SELECT COUNT(*) FROM compras WHERE estado IN ('pedido', 'en_transito')"),
    })


@bp.get("/parts")
@staff_permission("almacen")
def list_parts():
    query = parse_query(PartListQuery)
    page, limit, offset = pagination()
    conditions, params = ["p.activo = TRUE"], []
    if query.stock:
        conditions.append(f"{STOCK_STATUS_SQL} = %s")
        params.append(query.stock)
    if query.categoria_id:
        conditions.append("p.categoria_id = %s")
        params.append(query.categoria_id)
    if query.q:
        conditions.append("(LOWER(p.nombre) LIKE %s OR LOWER(p.referencia) LIKE %s)")
        params += [f"%{query.q.lower()}%"] * 2
    where = " AND ".join(conditions)
    db = get_db()
    total = db.fetch_value(f"SELECT COUNT(*) FROM piezas p WHERE {where}", params)
    rows = db.fetch_all(f"{PART_SELECT} WHERE {where} ORDER BY p.stock - p.stock_minimo, p.nombre "
                        f"LIMIT {limit} OFFSET {offset}", params)
    return paginated([serialize_part(row) for row in rows], total, page)


@bp.post("/parts")
@staff_permission("almacen", write=True)
def create_part():
    data = parse_body(PartIn)
    db = get_db()
    with db.transaction():
        if db.fetch_value("SELECT id FROM piezas WHERE referencia = %s", (data.referencia,)):
            raise Conflict(f"Ya existe la referencia {data.referencia}", "PART_EXISTS")
        _validate_part_links(data.categoria_id, data.proveedor_id)
        part_id = db.execute(
            """INSERT INTO piezas (referencia, nombre, descripcion, marca, categoria_id, proveedor_id, precio_venta,
                                   coste, stock, stock_minimo, ubicacion)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s)""",
            (data.referencia, data.nombre, data.descripcion, data.marca, data.categoria_id, data.proveedor_id,
             data.precio_venta, data.coste, data.stock_minimo, data.ubicacion))
        if data.stock:
            move_stock(part_id, data.stock, "ajuste", current_user()["id"], reason="Stock inicial")
    return jsonify(serialize_part(_part(part_id))), 201


@bp.get("/parts/<int:part_id>")
@staff_permission("almacen")
def get_part(part_id: int):
    return jsonify(serialize_part(_part(part_id), detail=True))


@bp.patch("/parts/<int:part_id>")
@staff_permission("almacen", write=True)
def update_part(part_id: int):
    _part(part_id)
    changes = changed_fields(parse_body(PartUpdateIn))
    db = get_db()
    with db.transaction():
        _validate_part_links(changes.get("categoria_id"), changes.get("proveedor_id"))
        if changes:
            assignments = ", ".join(f"{column} = %s" for column in changes)
            db.execute(f"UPDATE piezas SET {assignments} WHERE id = %s", (*changes.values(), part_id))
    return jsonify(serialize_part(_part(part_id), detail=True))


@bp.patch("/parts/<int:part_id>/stock")
@staff_permission("almacen", write=True)
def adjust_stock(part_id: int):
    _part(part_id)
    data = parse_body(StockAdjustmentIn)
    db = get_db()
    with db.transaction():
        stock = move_stock(part_id, data.cantidad, "ajuste", current_user()["id"], reason=data.motivo)
        movement_id = db.fetch_value("SELECT MAX(id) FROM movimientos_stock WHERE pieza_id = %s", (part_id,))
    return jsonify({"pieza_id": part_id, "stock": stock, "movimiento_id": movement_id})


@bp.get("/parts/<int:part_id>/movements")
@staff_permission("almacen")
def part_movements(part_id: int):
    _part(part_id)
    page, limit, offset = pagination()
    db = get_db()
    total = db.fetch_value("SELECT COUNT(*) FROM movimientos_stock WHERE pieza_id = %s", (part_id,))
    rows = db.fetch_all(
        f"""SELECT m.id, m.tipo, m.cantidad, m.stock_resultante, m.pedido_id, m.compra_id, m.motivo, m.creado_en,
                   u.nombre AS usuario
              FROM movimientos_stock m LEFT JOIN usuarios u ON u.id = m.usuario_id
             WHERE m.pieza_id = %s ORDER BY m.creado_en DESC, m.id DESC LIMIT {limit} OFFSET {offset}""", (part_id,))
    return paginated(rows, total, page)


@bp.delete("/parts/<int:part_id>")
@staff_permission("almacen", write=True)
def retire_part(part_id: int):
    _part(part_id)
    db = get_db()
    with db.transaction():
        db.execute("UPDATE piezas SET activo = FALSE WHERE id = %s", (part_id,))
    return no_content()


@bp.get("/suppliers")
@staff_permission("almacen")
def list_suppliers():
    rows = get_db().fetch_all("SELECT id, nombre, cif, email, telefono, web FROM proveedores WHERE activo = TRUE "
                              "ORDER BY nombre")
    return jsonify({"data": rows, "total": len(rows), "page": 1})


@bp.post("/suppliers")
@staff_permission("almacen", write=True)
def create_supplier():
    data = parse_body(SupplierIn)
    db = get_db()
    with db.transaction():
        if db.fetch_value("SELECT id FROM proveedores WHERE LOWER(nombre) = LOWER(%s)", (data.nombre,)):
            raise Conflict("Ese proveedor ya existe", "SUPPLIER_EXISTS")
        supplier_id = db.execute("INSERT INTO proveedores (nombre, cif, email, telefono, web) VALUES (%s, %s, %s, %s, %s)",
                                 (data.nombre, data.cif, data.email, data.telefono, data.web))
    return jsonify({"id": supplier_id, **data.model_dump()}), 201


def _purchase(purchase_id: int) -> dict:
    db = get_db()
    purchase = db.fetch_one(
        """SELECT c.id, c.proveedor_id, pr.nombre AS proveedor, c.estado, c.fecha_pedido, c.entrega_prevista,
                  c.recibido_en, c.notas, c.creado_en
             FROM compras c JOIN proveedores pr ON pr.id = c.proveedor_id WHERE c.id = %s""", (purchase_id,))
    if purchase is None:
        raise NotFound("Compra no encontrada")
    lines = db.fetch_all(
        """SELECT cl.pieza_id, p.referencia AS pieza, p.nombre, cl.cantidad, cl.coste_unitario, cl.cantidad_recibida
             FROM compra_lineas cl JOIN piezas p ON p.id = cl.pieza_id WHERE cl.compra_id = %s""", (purchase_id,))
    purchase["codigo"] = purchase_code(purchase_id)
    purchase["lineas"] = [{**line, "coste_unitario": money(line["coste_unitario"])} for line in lines]
    return purchase


@bp.get("/purchase-orders")
@staff_permission("almacen")
def list_purchases():
    state = request.args.get("estado")
    db = get_db()
    if state:
        ids = db.fetch_all("SELECT id FROM compras WHERE estado = %s ORDER BY fecha_pedido DESC", (state,))
    else:
        ids = db.fetch_all("SELECT id FROM compras ORDER BY fecha_pedido DESC")
    data = [_purchase(row["id"]) for row in ids]
    return jsonify({"data": data, "total": len(data), "page": 1})


@bp.post("/purchase-orders")
@staff_permission("almacen", write=True)
def create_purchase():
    data = parse_body(PurchaseIn)
    db = get_db()
    with db.transaction():
        if not db.fetch_value("SELECT id FROM proveedores WHERE id = %s AND activo = TRUE", (data.proveedor_id,)):
            raise NotFound("Proveedor no encontrado")
        part_ids = [line.pieza_id for line in data.lineas]
        found = {row["id"] for row in db.fetch_all(
            f"SELECT id FROM piezas WHERE id IN ({placeholders(len(part_ids))})", part_ids)}
        missing = {f"lineas[{index}].pieza_id": "No existe" for index, line in enumerate(data.lineas)
                   if line.pieza_id not in found}
        if missing:
            raise BadRequest("Pieza no encontrada", fields=missing)
        purchase_id = db.execute(
            """INSERT INTO compras (proveedor_id, estado, fecha_pedido, entrega_prevista, notas, creado_por)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (data.proveedor_id, data.estado, now().date(), data.entrega_prevista, data.notas, current_user()["id"]))
        for line in data.lineas:
            db.execute("INSERT INTO compra_lineas (compra_id, pieza_id, cantidad, coste_unitario) VALUES (%s, %s, %s, %s)",
                       (purchase_id, line.pieza_id, line.cantidad, line.coste_unitario))
    return jsonify(_purchase(purchase_id)), 201


@bp.patch("/purchase-orders/<int:purchase_id>")
@staff_permission("almacen", write=True)
def update_purchase(purchase_id: int):
    purchase = _purchase(purchase_id)
    if purchase["estado"] in ("recibido", "cancelado"):
        raise Conflict("La compra ya está cerrada", "PURCHASE_CLOSED")
    changes = changed_fields(parse_body(PurchaseUpdateIn))
    db = get_db()
    with db.transaction():
        if changes:
            assignments = ", ".join(f"{column} = %s" for column in changes)
            db.execute(f"UPDATE compras SET {assignments} WHERE id = %s", (*changes.values(), purchase_id))
    return jsonify(_purchase(purchase_id))


@bp.post("/purchase-orders/<int:purchase_id>/receive")
@staff_permission("almacen", write=True)
def receive_purchase(purchase_id: int):
    data = parse_body(ReceivePurchaseIn)
    db = get_db()
    with db.transaction():
        purchase = db.fetch_one("SELECT id, estado FROM compras WHERE id = %s FOR UPDATE", (purchase_id,))
        if purchase is None:
            raise NotFound("Compra no encontrada")
        if purchase["estado"] == "recibido":
            raise Conflict("Esta compra ya se recibió", "ALREADY_RECEIVED")
        if purchase["estado"] == "cancelado":
            raise Conflict("La compra está cancelada", "PURCHASE_CLOSED")
        lines = {row["pieza_id"]: row for row in db.fetch_all(
            "SELECT pieza_id, cantidad, cantidad_recibida FROM compra_lineas WHERE compra_id = %s", (purchase_id,))}
        received = ({line.pieza_id: line.cantidad_recibida for line in data.lineas} if data.lineas
                    else {part_id: line["cantidad"] - line["cantidad_recibida"] for part_id, line in lines.items()})
        unknown = [part_id for part_id in received if part_id not in lines]
        if unknown:
            raise BadRequest("Hay piezas que no están en la compra", fields={"lineas": f"Piezas ajenas: {unknown}"})
        updated_stock = []
        for part_id, quantity in received.items():
            line = lines[part_id]
            if line["cantidad_recibida"] + quantity > line["cantidad"]:
                raise BadRequest("Se recibe más de lo pedido", fields={"lineas": f"Pieza {part_id}"})
            if quantity:
                stock = move_stock(part_id, quantity, "entrada_compra", current_user()["id"], purchase_id=purchase_id,
                                   reason=f"Recepción {purchase_code(purchase_id)}")
                db.execute("UPDATE compra_lineas SET cantidad_recibida = cantidad_recibida + %s "
                           "WHERE compra_id = %s AND pieza_id = %s", (quantity, purchase_id, part_id))
                updated_stock.append({"pieza_id": part_id, "stock": stock})
        complete = all(lines[part_id]["cantidad_recibida"] + received.get(part_id, 0) >= lines[part_id]["cantidad"]
                       for part_id in lines)
        new_state = "recibido" if complete else "en_transito"
        db.execute("UPDATE compras SET estado = %s, recibido_en = %s WHERE id = %s",
                   (new_state, now() if complete else None, purchase_id))
    return jsonify({"id": purchase_id, "estado": new_state, "stock_actualizado": updated_stock})


@bp.delete("/purchase-orders/<int:purchase_id>")
@staff_permission("almacen", write=True)
def cancel_purchase(purchase_id: int):
    purchase = _purchase(purchase_id)
    if purchase["estado"] == "recibido" or any(line["cantidad_recibida"] for line in purchase["lineas"]):
        raise Conflict("No se puede cancelar una compra recibida", "ALREADY_RECEIVED")
    db = get_db()
    with db.transaction():
        db.execute("UPDATE compras SET estado = 'cancelado' WHERE id = %s", (purchase_id,))
    return no_content()
