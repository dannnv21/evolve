import sqlite3

import pytest



def test_assigning_parts_updates_stock_and_warns_when_low(client, admin_headers):
    too_many = client.post("/api/v1/admin/orders/2/parts", json={"pieza_id": 5, "cantidad": 3}, headers=admin_headers)
    assert too_many.status_code == 409 and too_many.get_json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert client.get("/api/v1/admin/parts/5", headers=admin_headers).get_json()["stock"] == 1

    assigned = client.post("/api/v1/admin/orders/2/parts", json={"pieza_id": 3, "cantidad": 2}, headers=admin_headers)
    assert assigned.status_code == 201 and assigned.get_json()["stock_restante"] == 2
    part = client.get("/api/v1/admin/parts/3", headers=admin_headers).get_json()
    assert part["stock"] == 2 and part["estado_stock"] == "critico"

    notifications = client.get("/api/v1/notifications", headers=admin_headers).get_json()["data"]
    assert any(item["tipo"] == "stock_bajo" and item["pieza_id"] == 3 for item in notifications)

    assert client.delete("/api/v1/admin/orders/2/parts/3", headers=admin_headers).status_code == 204
    assert client.get("/api/v1/admin/parts/3", headers=admin_headers).get_json()["stock"] == 4
    movements = client.get("/api/v1/admin/parts/3/movements", headers=admin_headers).get_json()["data"]
    assert [movement["tipo"] for movement in movements[:2]] == ["devolucion", "salida_pedido"]


def test_receiving_a_purchase_adds_stock(client, admin_headers):
    received = client.post("/api/v1/admin/purchase-orders/1/receive", json={}, headers=admin_headers)
    assert received.get_json()["estado"] == "recibido"
    assert client.get("/api/v1/admin/parts/1", headers=admin_headers).get_json()["stock"] == 4
    assert client.post("/api/v1/admin/purchase-orders/1/receive", json={}, headers=admin_headers).status_code == 409


def test_calendar_rejects_overlapping_events(client, admin_headers):
    event = {"tipo": "trabajo", "empleado_id": 3, "titulo": "Prueba",
             "inicio": "2026-11-24T10:00:00", "fin": "2026-11-24T11:00:00"}
    overlap = client.post("/api/v1/admin/calendar/events", json=event, headers=admin_headers)
    assert overlap.status_code == 409
    assert overlap.get_json()["error"]["fields"]["conflicto"]["cita_id"] == 2

    free = client.post("/api/v1/admin/calendar/events", headers=admin_headers,
                       json={**event, "inicio": "2026-11-24T15:00:00", "fin": "2026-11-24T16:00:00"})
    assert free.status_code == 201
    moved = client.patch(f"/api/v1/admin/calendar/events/{free.get_json()['id']}", headers=admin_headers,
                         json={"inicio": "2026-11-24T13:00:00", "fin": "2026-11-24T14:30:00"})
    assert moved.status_code == 409

    cancelled = client.delete("/api/v1/admin/calendar/events/2", headers=admin_headers)
    assert cancelled.status_code == 204
    assert client.post("/api/v1/admin/calendar/events", json=event, headers=admin_headers).status_code == 201


def test_database_trigger_also_blocks_overlaps(app):
    connection = sqlite3.connect(app.config["DATABASE_URL"].replace("sqlite:///", ""))
    with pytest.raises(sqlite3.IntegrityError, match="Franja horaria"):
        connection.execute("INSERT INTO citas (tipo, empleado_id, titulo, inicio, fin) "
                           "VALUES ('trabajo', 3, 'Directo', '2026-11-24 10:00:00', '2026-11-24 11:00:00')")
    with pytest.raises(sqlite3.IntegrityError, match="Stock insuficiente"):
        connection.execute("INSERT INTO movimientos_stock (pieza_id, tipo, cantidad) VALUES (5, 'ajuste', -5)")


def test_invalid_event_range_is_rejected(client, admin_headers):
    response = client.post("/api/v1/admin/calendar/events", headers=admin_headers, json={
        "tipo": "otro", "titulo": "Mal", "inicio": "2026-11-24T11:00:00", "fin": "2026-11-24T10:00:00"})
    assert response.status_code == 400


def test_service_catalog_management(client, admin_headers):
    created = client.post("/api/v1/admin/services", headers=admin_headers,
                          json={"nombre": "Tintado de lunas", "precio_base": 190, "horas_estimadas": 3})
    assert created.status_code == 201
    service_id = created.get_json()["id"]
    assert client.post("/api/v1/admin/services", headers=admin_headers,
                       json={"nombre": "tintado de lunas", "precio_base": 1}).status_code == 409
    assert client.patch(f"/api/v1/admin/services/{service_id}/status", json={"activo": False},
                        headers=admin_headers).status_code == 200
    public = client.get("/api/v1/services").get_json()["data"]
    assert service_id not in [service["id"] for service in public]


def test_employee_lifecycle(client, admin_headers):
    created = client.post("/api/v1/admin/employees", headers=admin_headers, json={
        "nombre": "Iván", "apellidos": "Torres", "email": "ivan@evolve.test", "rol": "empleado", "puesto_id": 5,
        "fecha_alta": "2026-12-01", "password_temporal": "Temp2026!"})
    assert created.status_code == 201
    employee_id = created.get_json()["id"]
    assert client.post("/api/v1/auth/login",
                       json={"email": "ivan@evolve.test", "password": "Temp2026!"}).status_code == 200
    assert client.patch(f"/api/v1/admin/employees/{employee_id}/availability", headers=admin_headers,
                        json={"disponibilidad": "ausente"}).status_code == 400
    busy = client.delete("/api/v1/admin/employees/3", headers=admin_headers)
    assert busy.status_code == 409
    assert client.delete(f"/api/v1/admin/employees/{employee_id}", headers=admin_headers).status_code == 204
    assert client.post("/api/v1/auth/login",
                       json={"email": "ivan@evolve.test", "password": "Temp2026!"}).status_code == 401


def test_unavailable_employee_cannot_be_assigned(client, admin_headers):
    response = client.put("/api/v1/admin/orders/3/assignee", json={"empleado_id": 5}, headers=admin_headers)
    assert response.status_code == 409 and "27/11" in response.get_json()["error"]["message"]


def test_reports_are_generated_as_csv(client, admin_headers):
    created = client.post("/api/v1/admin/reports", headers=admin_headers, json={
        "tipo": "facturacion", "periodo_inicio": "2026-10-01", "periodo_fin": "2026-11-30", "nombre": "Facturación"})
    assert created.status_code == 201 and created.get_json()["filas"] == 3
    download = client.get(f"/api/v1/admin/reports/{created.get_json()['id']}/download", headers=admin_headers)
    assert download.status_code == 200 and b"EV-0004" in download.data
    pdf = client.post("/api/v1/admin/reports", headers=admin_headers, json={
        "tipo": "almacen", "formato": "pdf", "periodo_inicio": "2026-10-01", "periodo_fin": "2026-11-30", "nombre": "x"})
    assert pdf.status_code == 400


def test_dashboard_and_settings(client, admin_headers):
    summary = client.get("/api/v1/admin/dashboard/summary", headers=admin_headers).get_json()
    assert summary["esperando_piezas"]["valor"] == 1
    saved = client.put("/api/v1/admin/settings/business", json={"capacidad_maxima": 12}, headers=admin_headers)
    assert saved.get_json()["capacidad_maxima"] == 12 and "ocupacion_actual" in saved.get_json()
    assert client.get("/api/v1/workshop").get_json()["nombre"].startswith("Evolve")
