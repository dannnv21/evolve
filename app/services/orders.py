from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from flask import current_app

from ..constants import ORDER_TRANSITIONS
from ..db import get_db, placeholders
from ..errors import BadRequest, Conflict, NotFound
from ..http import json_value, money, now, order_code
from ..security import current_employee_id, current_user, is_restricted_employee
from ..storage import signed_url
from .notifications import notify_order_client

ORDER_SELECT = """
    SELECT p.*, v.marca, v.modelo, v.variante, v.anio, v.matricula,
           c.nombre AS cliente_nombre, c.apellidos AS cliente_apellidos, c.email AS cliente_email,
           c.telefono AS cliente_telefono,
           e.id AS responsable_empleado_id, ru.nombre AS responsable_nombre, ru.apellidos AS responsable_apellidos
      FROM pedidos p
      JOIN vehiculos v ON v.id = p.vehiculo_id
      JOIN usuarios c ON c.id = p.cliente_id
      LEFT JOIN empleados e ON e.id = p.responsable_id
      LEFT JOIN usuarios ru ON ru.id = e.usuario_id
"""


def get_client_order(order_id: int, for_update: bool = False) -> dict:
    sql = "SELECT * FROM pedidos WHERE id = %s AND cliente_id = %s" + (" FOR UPDATE" if for_update else "")
    order = get_db().fetch_one(sql, (order_id, current_user()["id"]))
    if order is None:
        raise NotFound("Pedido no encontrado")
    return order


def get_staff_order(order_id: int, for_update: bool = False) -> dict:
    sql = "SELECT * FROM pedidos WHERE id = %s AND estado <> 'borrador'" + (" FOR UPDATE" if for_update else "")
    order = get_db().fetch_one(sql, (order_id,))
    if order is None or (is_restricted_employee() and order["responsable_id"] != current_employee_id()):
        raise NotFound("Pedido no encontrado")
    return order


def ensure_draft(order: dict) -> None:
    if order["estado"] != "borrador":
        raise Conflict("Solo se puede editar un pedido en borrador", "ORDER_NOT_EDITABLE")


def modifications_for(order_ids: list[int]) -> dict[int, list[dict]]:
    if not order_ids:
        return {}
    rows = get_db().fetch_all(
        f"""SELECT pm.pedido_id, t.codigo AS tipo, t.nombre, pm.detalle
              FROM pedido_modificaciones pm JOIN tipos_modificacion t ON t.id = pm.tipo_modificacion_id
             WHERE pm.pedido_id IN ({placeholders(len(order_ids))}) ORDER BY t.id""", order_ids)
    grouped: dict[int, list[dict]] = {order_id: [] for order_id in order_ids}
    for row in rows:
        grouped[row["pedido_id"]].append({"tipo": row["tipo"], "nombre": row["nombre"], "detalle": row["detalle"]})
    return grouped


def latest_quote(order_id: int, include_drafts: bool = False) -> Optional[dict]:
    condition = "" if include_drafts else "AND estado <> 'borrador'"
    return get_db().fetch_one(
        f"SELECT * FROM presupuestos WHERE pedido_id = %s {condition} ORDER BY numero DESC LIMIT 1", (order_id,))


def serialize_quote(quote: dict, with_lines: bool = True) -> dict:
    data = {"id": quote["id"], "pedido_id": quote["pedido_id"], "numero": quote["numero"],
            "estado": quote["estado"], "base_imponible": money(quote["base_imponible"]),
            "iva_pct": money(quote["iva_pct"]), "total": money(quote["total"]),
            "valido_hasta": quote["valido_hasta"], "enviado_en": quote["enviado_en"],
            "respondido_en": quote["respondido_en"], "motivo_rechazo": quote["motivo_rechazo"]}
    if with_lines:
        lines = get_db().fetch_all(
            """SELECT id, tipo, servicio_id, pieza_id, descripcion, cantidad, precio_unitario, importe
                 FROM presupuesto_lineas WHERE presupuesto_id = %s ORDER BY id""", (quote["id"],))
        data["lineas"] = [{**line, "cantidad": money(line["cantidad"]),
                           "precio_unitario": money(line["precio_unitario"]),
                           "importe": money(line["importe"])} for line in lines]
    return data


def serialize_order(row: dict, modifications: Optional[list[dict]] = None, detail: bool = False) -> dict:
    order = {
        "id": row["id"], "codigo": order_code(row["id"]), "estado": row["estado"], "progreso": row["progreso"],
        "vehiculo": {"id": row["vehiculo_id"], "marca": row["marca"], "modelo": row["modelo"],
                     "variante": row["variante"], "anio": row["anio"], "matricula": row["matricula"]},
        "modificaciones": modifications or [],
        "fecha_preferida": row["fecha_preferida"], "fecha_entrada": row["fecha_entrada"],
        "entrega_estimada": row["entrega_estimada"], "entrega_real": row["entrega_real"],
        "responsable": ({"empleado_id": row["responsable_empleado_id"],
                         "nombre": f"{row['responsable_nombre']} {row['responsable_apellidos']}"}
                        if row.get("responsable_empleado_id") else None),
        "creado_en": row["creado_en"],
    }
    quote = latest_quote(row["id"])
    order["presupuesto"] = ({"id": quote["id"], "numero": quote["numero"], "estado": quote["estado"],
                             "total": money(quote["total"])} if quote else None)
    if detail:
        order.update(
            descripcion_cliente=row["descripcion_cliente"],
            especificaciones=json_value(row["especificaciones"]),
            version_actual_id=row["version_actual_id"], version_aprobada_id=row["version_aprobada_id"],
            motivo_rechazo_taller=row["motivo_rechazo_taller"], enviado_en=row["enviado_en"],
            referencias=order_references(row["id"]))
    return order


def order_references(order_id: int) -> list[dict]:
    rows = get_db().fetch_all(
        """SELECT r.id, t.codigo AS tipo_modificacion, r.archivo_id, r.pieza_id, pz.referencia AS pieza_referencia,
                  r.marca, r.modelo, r.notas
             FROM referencias_pieza r
             JOIN tipos_modificacion t ON t.id = r.tipo_modificacion_id
             LEFT JOIN piezas pz ON pz.id = r.pieza_id
            WHERE r.pedido_id = %s ORDER BY r.id""", (order_id,))
    return [{"id": row["id"], "tipo_modificacion": row["tipo_modificacion"], "marca": row["marca"],
             "modelo": row["modelo"], "notas": row["notas"], "pieza_id": row["pieza_id"],
             "pieza_catalogo": row["pieza_referencia"], "url": signed_url(row["archivo_id"])} for row in rows]


def load_order_rows(where_sql: str, params: tuple, order_by: str = "p.creado_en DESC",
                    limit: Optional[int] = None, offset: int = 0) -> list[dict]:
    sql = f"{ORDER_SELECT} WHERE {where_sql} ORDER BY {order_by}"
    if limit is not None:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return get_db().fetch_all(sql, params)


def order_detail(order_id: int) -> dict:
    rows = load_order_rows("p.id = %s", (order_id,))
    if not rows:
        raise NotFound("Pedido no encontrado")
    return serialize_order(rows[0], modifications_for([order_id])[order_id], detail=True)


def add_history(order_id: int, previous_state: Optional[str], new_state: str, note: Optional[str] = None,
                progress: Optional[int] = None, visible_to_client: bool = True) -> None:
    get_db().execute(
        """INSERT INTO pedido_historial (pedido_id, estado_anterior, estado_nuevo, progreso, nota,
                                         visible_cliente, usuario_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (order_id, previous_state, new_state, progress, note, visible_to_client, current_user()["id"]))


def set_modifications(order_id: int, modifications: list) -> None:
    db = get_db()
    codes = [item.tipo for item in modifications]
    types = {row["codigo"]: row["id"] for row in db.fetch_all(
        f"SELECT id, codigo FROM tipos_modificacion WHERE activo = TRUE AND codigo IN ({placeholders(len(codes))})",
        codes)} if codes else {}
    unknown = {f"modificaciones[{index}].tipo": "No existe"
               for index, item in enumerate(modifications) if item.tipo not in types}
    if unknown:
        raise BadRequest("Tipo de modificación no válido", fields=unknown)
    db.execute("DELETE FROM pedido_modificaciones WHERE pedido_id = %s", (order_id,))
    seen = set()
    for item in modifications:
        if item.tipo in seen:
            continue
        seen.add(item.tipo)
        db.execute("INSERT INTO pedido_modificaciones (pedido_id, tipo_modificacion_id, detalle) VALUES (%s, %s, %s)",
                   (order_id, types[item.tipo], item.detalle))


def change_status(order: dict, new_state: str, progress: Optional[int], note: Optional[str],
                  visible_to_client: bool) -> dict:
    current_state = order["estado"]
    if new_state not in ORDER_TRANSITIONS.get(current_state, set()):
        raise Conflict(f"No se puede pasar de {current_state} a {new_state}", "INVALID_TRANSITION")
    timestamp = now()
    progress = 100 if new_state in ("listo", "entregado") else (progress if progress is not None else order["progreso"])
    fields = {"estado": new_state, "progreso": progress}
    if new_state == "en_proceso" and order["fecha_entrada"] is None:
        fields["fecha_entrada"] = timestamp
    if new_state == "entregado":
        fields["entrega_real"] = timestamp
    if new_state == "cancelado":
        get_db().execute("UPDATE citas SET estado = 'cancelada' WHERE pedido_id = %s AND estado = 'programada'",
                         (order["id"],))
    assignments = ", ".join(f"{column} = %s" for column in fields)
    get_db().execute(f"UPDATE pedidos SET {assignments} WHERE id = %s", (*fields.values(), order["id"]))
    add_history(order["id"], current_state, new_state, note, progress, visible_to_client)
    if visible_to_client:
        label = new_state.replace("_", " ")
        notify_order_client(order, f"Tu pedido {order_code(order['id'])} está {label}",
                            note or f"Progreso: {progress} %")
    return {"id": order["id"], "estado": new_state, "progreso": progress}


def quote_totals(lines: list[dict], vat_percent: Decimal) -> tuple[Decimal, Decimal]:
    base = sum((money(line["cantidad"] * line["precio_unitario"]) for line in lines), Decimal("0"))
    total = money(base * (1 + vat_percent / Decimal(100)))
    return money(base), total


def default_quote_expiry() -> date:
    return date.today() + timedelta(days=current_app.config["QUOTE_VALID_DAYS"])


def paid_amount(order_id: int) -> Decimal:
    return money(get_db().fetch_value(
        "SELECT COALESCE(SUM(importe), 0) FROM pagos WHERE pedido_id = %s AND concepto = 'pedido' "
        "AND estado = 'completado'", (order_id,)))
