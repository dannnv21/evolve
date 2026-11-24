from typing import Iterable, Optional

from ..db import get_db


def notify(user_id: int, notification_type: str, title: str, body: Optional[str] = None,
           order_id: Optional[int] = None, part_id: Optional[int] = None,
           replica_id: Optional[int] = None) -> int:
    """Guarda el aviso en el centro de notificaciones del usuario."""
    return get_db().execute(
        """INSERT INTO notificaciones (usuario_id, tipo, titulo, cuerpo, pedido_id, pieza_id, replica_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (user_id, notification_type, title[:120], body[:255] if body else None, order_id, part_id, replica_id))


def staff_user_ids(roles: Iterable[str] = ("admin", "jefe_taller")) -> list[int]:
    roles = tuple(roles)
    marks = ", ".join(["%s"] * len(roles))
    rows = get_db().fetch_all(
        f"""SELECT u.id FROM usuarios u JOIN roles r ON r.id = u.rol_id
             WHERE r.codigo IN ({marks}) AND u.activo = TRUE AND u.eliminado_en IS NULL""", roles)
    return [row["id"] for row in rows]


def notify_staff(notification_type: str, title: str, body: Optional[str] = None, **links) -> None:
    for user_id in staff_user_ids():
        notify(user_id, notification_type, title, body, **links)


def notify_order_client(order: dict, title: str, body: Optional[str] = None,
                        notification_type: str = "estado_pedido") -> None:
    notify(order["cliente_id"], notification_type, title, body, order_id=order["id"])
