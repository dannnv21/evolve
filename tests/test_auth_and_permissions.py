from .conftest import ADMIN_EMAIL, CLIENT_EMAIL, EMPLOYEE_EMAIL, PASSWORD, login


def test_register_validates_and_rejects_duplicates(client):
    invalid = client.post("/api/v1/auth/register", json={
        "nombre": "Paula", "apellidos": "Serra", "email": "paula@correo.test",
        "password": "corta", "password_repetida": "corta"})
    assert invalid.status_code == 400
    assert "password" in invalid.get_json()["error"]["fields"]

    body = {"nombre": "Paula", "apellidos": "Serra Vidal", "email": "Paula@Correo.test",
            "password": "Evolve2026!", "password_repetida": "Evolve2026!"}
    created = client.post("/api/v1/auth/register", json=body)
    assert created.status_code == 201
    assert created.get_json()["nombre_usuario"] == "pserra"
    assert created.get_json()["email"] == "paula@correo.test"

    duplicate = client.post("/api/v1/auth/register", json=body)
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"]["code"] == "EMAIL_EXISTS"


def test_login_refresh_and_logout(client):
    wrong = client.post("/api/v1/auth/login", json={"email": CLIENT_EMAIL, "password": "Incorrecta1"})
    assert wrong.status_code == 401

    tokens = client.post("/api/v1/auth/login", json={"email": CLIENT_EMAIL, "password": PASSWORD}).get_json()
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200 and refreshed.get_json()["access_token"]

    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]},
                       headers=headers).status_code == 204
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_password_reset_flow(app, client):
    app.config["EXPOSE_RESET_TOKEN"] = True
    token = client.post("/api/v1/auth/forgot-password", json={"email": CLIENT_EMAIL}).get_json()["token_desarrollo"]
    reset = client.post("/api/v1/auth/reset-password",
                        json={"token": token, "password": "Nueva2026!", "password_repetida": "Nueva2026!"})
    assert reset.status_code == 200
    assert client.post("/api/v1/auth/reset-password",
                       json={"token": token, "password": "Otra2026!", "password_repetida": "Otra2026!"}).status_code == 400
    assert client.post("/api/v1/auth/login", json={"email": CLIENT_EMAIL, "password": "Nueva2026!"}).status_code == 200


def test_unknown_email_on_forgot_password_does_not_leak(client):
    response = client.post("/api/v1/auth/forgot-password", json={"email": "nadie@correo.test"})
    assert response.status_code == 200
    assert "token_desarrollo" not in response.get_json()


def test_access_control(client):
    assert client.get("/api/v1/orders").status_code == 401
    assert client.get("/api/v1/orders", headers={"Authorization": "Bearer basura"}).status_code == 401

    client_headers = login(client, CLIENT_EMAIL)
    assert client.get("/api/v1/admin/orders", headers=client_headers).status_code == 403

    employee_headers = login(client, EMPLOYEE_EMAIL)
    assert client.get("/api/v1/admin/users", headers=employee_headers).status_code == 403
    assert client.get("/api/v1/admin/parts", headers=employee_headers).status_code == 200
    assert client.post("/api/v1/admin/parts", json={}, headers=employee_headers).status_code == 403


def test_permission_matrix_is_applied(client):
    admin_headers = login(client, ADMIN_EMAIL)
    employee_headers = login(client, EMPLOYEE_EMAIL)
    assert client.put("/api/v1/admin/settings/permissions", headers=admin_headers,
                      json={"roles": [{"rol": "empleado", "almacen": "ninguno"}]}).status_code == 200
    assert client.get("/api/v1/admin/parts", headers=employee_headers).status_code == 403


def test_employee_only_sees_assigned_orders(client):
    headers = login(client, EMPLOYEE_EMAIL)
    orders = client.get("/api/v1/admin/orders", headers=headers).get_json()["data"]
    assert [order["id"] for order in orders] == [2]
    assert client.get("/api/v1/admin/orders/1", headers=headers).status_code == 404


def test_disabled_account_cannot_use_its_token(client, admin_headers):
    headers = login(client, EMPLOYEE_EMAIL)
    assert client.patch("/api/v1/admin/users/3/status", json={"activo": False}, headers=admin_headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_last_admin_is_protected(client, admin_headers):
    response = client.patch("/api/v1/admin/users/1/status", json={"activo": False}, headers=admin_headers)
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "LAST_ADMIN"


def test_errors_use_json_format(client):
    not_found = client.get("/api/v1/no-existe")
    assert not_found.status_code == 404 and not_found.get_json()["error"]["code"] == "ROUTE_NOT_FOUND"
    assert client.delete("/api/v1/services").status_code == 405
    bad_json = client.post("/api/v1/auth/login", data="{no es json", content_type="application/json")
    assert bad_json.status_code == 400
