"""Movimientos de almacén.

Cada movimiento se inserta en movimientos_stock y el trigger de la base de
datos actualiza piezas.stock (y rechaza dejarlo por debajo de 0). Aquí se
comprueba antes para devolver un error claro.
"""
from typing import Optional

from ..db import get_db
from ..errors import Conflict, NotFound
from .notifications import notify_staff
from .settings import get_setting


def lock_part(part_id: int) -> dict:
    part = get_db().fetch_one(
        "SELECT id, referencia, nombre, stock, stock_minimo, activo FROM piezas WHERE id = %s FOR UPDATE",
        (part_id,))
    if part is None:
        raise NotFound("Pieza no encontrada")
    return part


def move_stock(part_id: int, quantity: int, movement_type: str, user_id: Optional[int],
               order_id: Optional[int] = None, purchase_id: Optional[int] = None,
               reason: Optional[str] = None) -> int:
    """Registra el movimiento y devuelve el stock resultante."""
    db = get_db()
    part = lock_part(part_id)
    resulting_stock = part["stock"] + quantity
    if resulting_stock < 0:
        raise Conflict(f"Solo quedan {part['stock']} uds. de {part['nombre']}", "INSUFFICIENT_STOCK")
    db.execute(
        """INSERT INTO movimientos_stock (pieza_id, tipo, cantidad, pedido_id, compra_id, motivo, usuario_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (part_id, movement_type, quantity, order_id, purchase_id, reason, user_id))
    if quantity < 0 and resulting_stock <= part["stock_minimo"] < part["stock"]:
        _warn_low_stock(part, resulting_stock)
    return resulting_stock


def _warn_low_stock(part: dict, stock: int) -> None:
    if not get_setting("avisos_automaticos").get("alertas_almacen", True):
        return
    notify_staff("stock_bajo", "Stock por debajo del mínimo", f"{part['nombre']} · {stock} uds.", part_id=part["id"])


def stock_status(stock: int, minimum: int) -> str:
    if stock <= minimum:
        return "critico"
    if stock <= minimum * 2:
        return "bajo"
    return "correcto"


STOCK_STATUS_SQL = """CASE WHEN p.stock <= p.stock_minimo THEN 'critico'
                           WHEN p.stock <= p.stock_minimo * 2 THEN 'bajo'
                           ELSE 'correcto' END"""
