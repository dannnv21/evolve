from datetime import date, timedelta

from .conftest import image_file, login


def next_working_day() -> date:
    day = date.today() + timedelta(days=1)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return day


def create_submitted_order(client, headers, slot_start: str, slot_end: str):
    order_id = client.post("/api/v1/orders", json={"vehiculo_id": 5}, headers=headers).get_json()["id"]
    client.patch(f"/api/v1/orders/{order_id}", headers=headers,
                 json={"modificaciones": [{"tipo": "llantas", "detalle": "18 negro mate"}]})
    response = client.post(f"/api/v1/orders/{order_id}/submit", headers=headers,
                           json={"franja_entrada": {"inicio": slot_start, "fin": slot_end}})
    return order_id, response


def test_client_creates_order_in_four_steps(client, client_headers):
    created = client.post("/api/v1/orders", json={"vehiculo_id": 5}, headers=client_headers)
    assert created.status_code == 201
    order_id = created.get_json()["id"]

    photo, name = image_file("aleron.jpg")
    reference = client.post(f"/api/v1/orders/{order_id}/references", headers=client_headers,
                            data={"tipo_modificacion": "kit_exterior", "modelo": "Alerón GT", "archivo": (photo, name)},
                            content_type="multipart/form-data")
    assert reference.status_code == 201
    assert reference.get_json()["url"].startswith("http")

    empty_submit = client.post(f"/api/v1/orders/{order_id}/submit", json={}, headers=client_headers)
    assert empty_submit.status_code == 400

    draft = client.patch(f"/api/v1/orders/{order_id}", headers=client_headers, json={
        "modificaciones": [{"tipo": "kit_exterior", "detalle": "Alerón GT"}, {"tipo": "llantas"}],
        "fecha_preferida": next_working_day().isoformat(), "especificaciones": {"aleron": "carbono"}})
    assert draft.status_code == 200 and draft.get_json()["modificaciones"] == 2

    submitted = client.post(f"/api/v1/orders/{order_id}/submit", json={}, headers=client_headers)
    assert submitted.status_code == 200 and submitted.get_json()["estado"] == "solicitado"

    assert client.patch(f"/api/v1/orders/{order_id}", json={"descripcion_cliente": "x"},
                        headers=client_headers).status_code == 409
    timeline = client.get(f"/api/v1/orders/{order_id}/timeline", headers=client_headers).get_json()["data"]
    assert timeline[-1]["estado"] == "solicitado"


def test_entry_slot_rule_prevents_double_booking(client, client_headers, admin_headers):
    day = next_working_day().isoformat()
    start, end = f"{day}T10:00:00", f"{day}T10:30:00"
    # Hay dos personas de recepción (admin y jefe de taller): la tercera reserva de la franja choca
    assert create_submitted_order(client, client_headers, start, end)[1].status_code == 200
    assert create_submitted_order(client, client_headers, start, end)[1].status_code == 200
    order_id, third = create_submitted_order(client, client_headers, start, end)
    assert third.status_code == 409
    assert third.get_json()["error"]["code"] == "SLOT_TAKEN"
    assert client.get(f"/api/v1/orders/{order_id}", headers=client_headers).get_json()["estado"] == "borrador"

    slots = client.get(f"/api/v1/availability/slots?date={day}", headers=client_headers).get_json()["franjas"]
    assert next(slot for slot in slots if slot["inicio"] == "10:00")["libre"] is False
    assert next(slot for slot in slots if slot["inicio"] == "11:00")["libre"] is True


def test_slot_outside_opening_hours_is_rejected(client, client_headers):
    day = next_working_day().isoformat()
    _, response = create_submitted_order(client, client_headers, f"{day}T20:00:00", f"{day}T20:30:00")
    assert response.status_code == 400


def test_quote_acceptance_payment_and_rating(client, client_headers, admin_headers):
    assert client.post("/api/v1/admin/orders/3/accept", headers=admin_headers).status_code == 200
    quote = client.put("/api/v1/admin/orders/3/quote", headers=admin_headers, json={"lineas": [
        {"tipo": "servicio", "servicio_id": 5},
        {"tipo": "pieza", "pieza_id": 1, "cantidad": 1},
        {"tipo": "otro", "descripcion": "Transporte", "precio_unitario": 10}]})
    assert quote.status_code == 200
    assert quote.get_json()["base_imponible"] == 1750.00
    assert quote.get_json()["total"] == 2117.50
    assert client.post("/api/v1/admin/orders/3/quote/send", headers=admin_headers).status_code == 200

    sergio = login(client, "sergio@correo.test")
    assert client.post("/api/v1/orders/3/quote/accept", headers=sergio).get_json()["estado"] == "aceptado"
    assert client.post("/api/v1/orders/3/quote/accept", headers=sergio).status_code == 409

    too_much = client.post("/api/v1/orders/3/payment", json={"importe": 5000}, headers=sergio)
    assert too_much.status_code == 400
    assert client.post("/api/v1/orders/3/payment", json={"importe": 1000}, headers=sergio).status_code == 201
    status = client.get("/api/v1/orders/3/payment", headers=sergio).get_json()
    assert status["pendiente"] == 1117.50
    assert client.post("/api/v1/orders/3/payment", json={"importe": 1117.50}, headers=sergio).status_code == 201
    assert client.post("/api/v1/orders/3/payment", json={"importe": 1}, headers=sergio).get_json()["error"]["code"] == "ALREADY_PAID"

    assert client.post("/api/v1/orders/3/rating", json={"puntuacion": 5}, headers=sergio).status_code == 409
    assert client.post("/api/v1/orders/6/rating", json={"puntuacion": 6}, headers=client_headers).status_code == 400
    assert client.post("/api/v1/orders/6/rating", json={"puntuacion": 5}, headers=client_headers).status_code == 201
    assert client.post("/api/v1/orders/6/rating", json={"puntuacion": 4}, headers=client_headers).status_code == 409


def test_rejected_quote_returns_order_for_a_new_quote(client, client_headers, admin_headers):
    rejected = client.post("/api/v1/orders/7/quote/reject", json={"motivo": "Caro"}, headers=client_headers)
    assert rejected.get_json()["estado"] == "solicitado"
    new_quote = client.put("/api/v1/admin/orders/7/quote", headers=admin_headers,
                           json={"lineas": [{"tipo": "servicio", "servicio_id": 9, "precio_unitario": 120}]})
    assert new_quote.get_json()["numero"] == 2


def test_status_transitions_are_enforced(client, admin_headers):
    invalid = client.patch("/api/v1/admin/orders/4/status", json={"estado": "en_proceso"}, headers=admin_headers)
    assert invalid.status_code == 409
    ready = client.patch("/api/v1/admin/orders/1/status", json={"estado": "listo"}, headers=admin_headers)
    assert ready.get_json() == {"id": 1, "estado": "listo", "progreso": 100}
    delivered = client.patch("/api/v1/admin/orders/1/status", json={"estado": "entregado"}, headers=admin_headers)
    assert delivered.status_code == 200
    marcos = login(client, "marcos@correo.test")
    notifications = client.get("/api/v1/notifications?unread=true", headers=marcos).get_json()
    assert any("entregado" in item["titulo"] for item in notifications["data"])


def test_client_cannot_see_other_clients_orders(client, client_headers):
    assert client.get("/api/v1/orders/1", headers=client_headers).status_code == 404
    assert client.get("/api/v1/orders/5", headers=client_headers).status_code == 200
