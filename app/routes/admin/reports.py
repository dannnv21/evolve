import csv
import io
from collections import Counter
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, jsonify, request, url_for

from ...db import get_db
from ...errors import BadRequest, NotFound
from ...http import as_datetime, money, no_content, order_code, pagination, paginated, parse_body
from ...schemas.workshop import ReportIn
from ...security import current_user, staff_permission
from ...storage import delete_file, read_file, save_bytes

bp = Blueprint("admin_reports", __name__, url_prefix="/admin")


def _months_back(months: int) -> list[str]:
    labels, cursor = [], date.today().replace(day=1)
    for _ in range(months):
        labels.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    return list(reversed(labels))


@bp.get("/reports/stats")
@staff_permission("informes")
def report_stats():
    try:
        months = min(max(int(request.args.get("months", 6)), 1), 24)
    except ValueError as error:
        raise BadRequest("months debe ser un número") from error
    labels = _months_back(months)
    start = datetime.strptime(labels[0] + "-01", "%Y-%m-%d")
    db = get_db()
    delivered = db.fetch_all("SELECT id, fecha_entrada, entrega_real FROM pedidos WHERE entrega_real >= %s", (start,))
    per_month = Counter(as_datetime(row["entrega_real"]).strftime("%Y-%m") for row in delivered)
    durations = [(as_datetime(row["entrega_real"]) - as_datetime(row["fecha_entrada"])).total_seconds() / 86400
                 for row in delivered if row["fecha_entrada"]]
    rating = db.fetch_one("SELECT AVG(puntuacion) AS media, COUNT(*) AS total FROM valoraciones WHERE creado_en >= %s",
                          (start,))
    revenue = money(db.fetch_value("SELECT COALESCE(SUM(importe), 0) FROM pagos WHERE estado = 'completado' "
                                   "AND pagado_en >= %s", (start,)))
    return jsonify({
        "pedidos_completados": [{"mes": label, "valor": per_month.get(label, 0)} for label in labels],
        "tiempo_medio_dias": round(sum(durations) / len(durations), 1) if durations else None,
        "satisfaccion": {"media": round(float(rating["media"]), 1) if rating["media"] is not None else None,
                         "valoraciones": rating["total"]},
        "facturacion_estimada": revenue,
        "ticket_medio": money(revenue / len(delivered)) if delivered else money(0),
    })


@bp.get("/ratings")
@staff_permission("informes")
def list_ratings():
    page, limit, offset = pagination()
    db = get_db()
    total = db.fetch_value("SELECT COUNT(*) FROM valoraciones")
    rows = db.fetch_all(
        f"""SELECT va.id, va.pedido_id, u.nombre, u.apellidos, va.puntuacion, va.comentario, va.creado_en
              FROM valoraciones va JOIN usuarios u ON u.id = va.cliente_id
             ORDER BY va.creado_en DESC LIMIT {limit} OFFSET {offset}""")
    data = [{"id": row["id"], "pedido": order_code(row["pedido_id"]), "cliente": f"{row['nombre']} {row['apellidos']}",
             "puntuacion": row["puntuacion"], "comentario": row["comentario"], "creado_en": row["creado_en"]}
            for row in rows]
    return paginated(data, total, page)


def _serialize_report(row: dict) -> dict:
    return {"id": row["id"], "nombre": row["nombre"], "tipo": row["tipo"], "formato": row["formato"],
            "periodo_inicio": row["periodo_inicio"], "periodo_fin": row["periodo_fin"], "creado_en": row["creado_en"],
            "descarga": url_for("admin_reports.download_report", report_id=row["id"])}


@bp.get("/reports")
@staff_permission("informes")
def list_reports():
    rows = get_db().fetch_all("SELECT * FROM informes ORDER BY creado_en DESC, id DESC")
    return jsonify({"data": [_serialize_report(row) for row in rows], "total": len(rows), "page": 1})


def _report_rows(report_type: str, start: datetime, end: datetime) -> tuple[list[str], list[list]]:
    db = get_db()
    if report_type == "rendimiento":
        rows = db.fetch_all(
            """SELECT p.id, u.nombre, u.apellidos, v.marca, v.modelo, p.estado, p.fecha_entrada, p.entrega_real
                 FROM pedidos p JOIN usuarios u ON u.id = p.cliente_id JOIN vehiculos v ON v.id = p.vehiculo_id
                WHERE p.entrega_real >= %s AND p.entrega_real < %s ORDER BY p.entrega_real""", (start, end))
        return (["pedido", "cliente", "vehiculo", "estado", "entrada", "entrega", "dias"],
                [[order_code(row["id"]), f"{row['nombre']} {row['apellidos']}", f"{row['marca']} {row['modelo']}",
                  row["estado"], row["fecha_entrada"], row["entrega_real"],
                  round((as_datetime(row["entrega_real"]) - as_datetime(row["fecha_entrada"])).total_seconds() / 86400, 1)
                  if row["fecha_entrada"] else ""] for row in rows])
    if report_type == "facturacion":
        rows = db.fetch_all(
            """SELECT id, concepto, pedido_id, metodo, importe, referencia, pagado_en FROM pagos
                WHERE estado = 'completado' AND pagado_en >= %s AND pagado_en < %s ORDER BY pagado_en""", (start, end))
        return (["pago", "concepto", "pedido", "metodo", "importe", "referencia", "fecha"],
                [[row["id"], row["concepto"], order_code(row["pedido_id"]), row["metodo"], money(row["importe"]),
                  row["referencia"], row["pagado_en"]] for row in rows])
    if report_type == "almacen":
        rows = db.fetch_all("SELECT referencia, nombre, stock, stock_minimo, precio_venta FROM piezas "
                            "WHERE activo = TRUE ORDER BY referencia")
        return (["referencia", "nombre", "stock", "stock_minimo", "precio_venta"],
                [[row["referencia"], row["nombre"], row["stock"], row["stock_minimo"], money(row["precio_venta"])]
                 for row in rows])
    rows = db.fetch_all(
        """SELECT va.pedido_id, u.nombre, u.apellidos, va.puntuacion, va.comentario, va.creado_en
             FROM valoraciones va JOIN usuarios u ON u.id = va.cliente_id
            WHERE va.creado_en >= %s AND va.creado_en < %s ORDER BY va.creado_en""", (start, end))
    return (["pedido", "cliente", "puntuacion", "comentario", "fecha"],
            [[order_code(row["pedido_id"]), f"{row['nombre']} {row['apellidos']}", row["puntuacion"],
              row["comentario"] or "", row["creado_en"]] for row in rows])


@bp.post("/reports")
@staff_permission("informes", write=True)
def create_report():
    data = parse_body(ReportIn)
    if data.formato != "csv":
        raise BadRequest("De momento solo se generan informes en CSV", "FORMAT_NOT_AVAILABLE")
    start = datetime.combine(data.periodo_inicio, datetime.min.time())
    end = datetime.combine(data.periodo_fin + timedelta(days=1), datetime.min.time())
    header, rows = _report_rows(data.tipo, start, end)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    content = buffer.getvalue().encode("utf-8-sig")
    db = get_db()
    with db.transaction():
        file_id = save_bytes(content, "informes", "text/csv", ".csv", current_user()["id"], f"{data.nombre}.csv")
        report_id = db.execute(
            """INSERT INTO informes (tipo, formato, nombre, periodo_inicio, periodo_fin, archivo_id, creado_por)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (data.tipo, data.formato, data.nombre, data.periodo_inicio, data.periodo_fin, file_id, current_user()["id"]))
    report = db.fetch_one("SELECT * FROM informes WHERE id = %s", (report_id,))
    return jsonify({**_serialize_report(report), "filas": len(rows)}), 201


@bp.get("/reports/<int:report_id>/download")
@staff_permission("informes")
def download_report(report_id: int):
    db = get_db()
    report = db.fetch_one("SELECT nombre, archivo_id FROM informes WHERE id = %s", (report_id,))
    if report is None or report["archivo_id"] is None:
        raise NotFound("Informe no encontrado")
    file_row = db.fetch_one("SELECT * FROM archivos WHERE id = %s", (report["archivo_id"],))
    filename = f"{report['nombre']}.csv".replace('"', "")
    return Response(read_file(file_row), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.delete("/reports/<int:report_id>")
@staff_permission("informes", write=True)
def delete_report(report_id: int):
    db = get_db()
    report = db.fetch_one("SELECT archivo_id FROM informes WHERE id = %s", (report_id,))
    if report is None:
        raise NotFound("Informe no encontrado")
    with db.transaction():
        db.execute("DELETE FROM informes WHERE id = %s", (report_id,))
        if report["archivo_id"]:
            delete_file(report["archivo_id"])
    return no_content()
