import json
import struct

from app.ai.models3d import Demo3DProvider
from app.ai.providers import DesignInputs, GeminiImageProvider, InputImage
from app.ai.worker import process_pending_jobs

from .conftest import image_file


def run_worker(app) -> int:
    with app.app_context():
        return process_pending_jobs()


def test_design_generation_creates_360_and_restore(app, client, client_headers):
    requested = client.post("/api/v1/orders/5/designs", headers=client_headers,
                            json={"peticion": "Alerón más alto", "version_padre_id": 2})
    assert requested.status_code == 202
    job_id, version_id = requested.get_json()["job_id"], requested.get_json()["version_id"]
    assert client.get(f"/api/v1/ai/jobs/{job_id}", headers=client_headers).get_json()["estado"] == "en_cola"

    assert run_worker(app) == 1
    job = client.get(f"/api/v1/ai/jobs/{job_id}", headers=client_headers).get_json()
    assert job["estado"] == "completado" and job["progreso"] == 100

    version = client.get(f"/api/v1/designs/{version_id}", headers=client_headers).get_json()
    angles = [frame["angulo"] for frame in version["fotogramas"]]
    assert angles == ["frontal", "frontal_izq", "lateral_izq", "trasera_izq",
                      "trasera", "trasera_der", "lateral_der", "frontal_der"]
    frame = client.get(version["fotogramas"][0]["url"])
    assert frame.status_code == 200 and frame.mimetype == "image/webp"

    order = client.get("/api/v1/orders/5", headers=client_headers).get_json()
    assert order["version_actual_id"] == version_id
    restored = client.post("/api/v1/orders/5/designs/restore", json={"version_id": 1}, headers=client_headers)
    assert restored.get_json()["version_actual_id"] == 1


def test_daily_generation_limit(client, client_headers):
    for _ in range(3):
        assert client.post("/api/v1/orders/5/designs", json={"peticion": "Otra prueba"},
                           headers=client_headers).status_code == 202
    blocked = client.post("/api/v1/orders/5/designs", json={"peticion": "Una más"}, headers=client_headers)
    assert blocked.status_code == 409 and blocked.get_json()["error"]["code"] == "DAILY_LIMIT"
    assert client.get("/api/v1/ai/usage", headers=client_headers).get_json()["restantes"] == 0


def test_design_needs_vehicle_photos(client, client_headers):
    vehicle_id = client.post("/api/v1/vehicles", headers=client_headers, json={
        "marca": "Seat", "modelo": "León", "matricula": "5521KPL"}).get_json()["id"]
    order_id = client.post("/api/v1/orders", json={"vehiculo_id": vehicle_id}, headers=client_headers).get_json()["id"]
    response = client.post(f"/api/v1/orders/{order_id}/designs", json={"peticion": "Vinilo negro"},
                           headers=client_headers)
    assert response.status_code == 400

    photo, name = image_file("trasera.jpg")
    uploaded = client.post(f"/api/v1/vehicles/{vehicle_id}/photos", headers=client_headers,
                           data={"angulo": "trasera", "archivo": (photo, name)}, content_type="multipart/form-data")
    assert uploaded.status_code == 201
    assert client.post(f"/api/v1/orders/{order_id}/designs", json={"peticion": "Vinilo negro"},
                       headers=client_headers).status_code == 202


def test_upload_rejects_files_that_are_not_images(client, client_headers):
    from io import BytesIO
    response = client.post("/api/v1/vehicles/5/photos", headers=client_headers, content_type="multipart/form-data",
                           data={"angulo": "frontal", "archivo": (BytesIO(b"no soy una imagen"), "virus.jpg")})
    assert response.status_code == 400


def test_replica_flow(app, client, client_headers, admin_headers):
    requested = client.post("/api/v1/replicas", headers=client_headers, json={
        "pedido_id": 5, "version_id": 2, "formato_entrega": "impresion_taller", "escala": "1:24", "material": "PETG"})
    assert requested.status_code == 202 and requested.get_json()["precio"] == 89.00
    replica_id = requested.get_json()["id"]
    assert client.post(f"/api/v1/replicas/{replica_id}/approve", headers=client_headers).status_code == 409

    run_worker(app)
    replica = client.get(f"/api/v1/replicas/{replica_id}", headers=client_headers).get_json()
    assert replica["estado"] == "modelo_listo" and replica["modelo_glb_url"]
    assert client.get(replica["modelo_glb_url"]).data[:4] == b"glTF"

    assert client.patch(f"/api/v1/admin/replicas/{replica_id}/status", json={"estado": "imprimiendo"},
                        headers=admin_headers).status_code == 409
    assert client.post(f"/api/v1/replicas/{replica_id}/approve", headers=client_headers).status_code == 200
    assert client.post(f"/api/v1/replicas/{replica_id}/payment", json={"importe": 89},
                       headers=client_headers).status_code == 400
    assert client.post(f"/api/v1/replicas/{replica_id}/payment", json={"importe": 107.69},
                       headers=client_headers).status_code == 201
    for state in ("imprimiendo", "lista", "entregada"):
        assert client.patch(f"/api/v1/admin/replicas/{replica_id}/status", json={"estado": state},
                            headers=admin_headers).status_code == 200
    assert client.delete(f"/api/v1/replicas/{replica_id}", headers=client_headers).status_code == 409


def test_printed_replica_requires_material(client, client_headers):
    response = client.post("/api/v1/replicas", headers=client_headers,
                           json={"pedido_id": 5, "version_id": 2, "formato_entrega": "impresion_taller"})
    assert response.status_code == 400


def test_demo_model_is_a_valid_glb():
    model = Demo3DProvider().generate([image_file()[0].getvalue()], "1:43")
    magic, version, total = struct.unpack("<4sII", model.glb[:12])
    assert (magic, version, total) == (b"glTF", 2, len(model.glb))
    json_length, chunk_type = struct.unpack("<I4s", model.glb[12:20])
    document = json.loads(model.glb[20:20 + json_length])
    assert chunk_type == b"JSON" and document["asset"]["version"] == "2.0"
    assert model.stl.startswith(b"solid") and model.preview[:4] == b"RIFF"


def test_gemini_provider_parses_responses(monkeypatch):
    provider = GeminiImageProvider("clave", "texto", "imagen")
    plan_reply = {"candidates": [{"content": {"parts": [{"text": json.dumps(
        {"piezas": ["gt wing"], "vista_maestra": "trasera_der", "prompts": {"trasera_der": "rear"}})}]}}]}
    image_reply = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "aGVsbG8="}}]}}]}
    calls = []

    def fake_call(model, parts, config=None):
        calls.append((model, config))
        return plan_reply if model == "texto" else image_reply

    monkeypatch.setattr(provider, "_call", fake_call)
    inputs = DesignInputs("Monta el alerón", [InputImage(b"x", "image/jpeg", "foto_vehiculo", "trasera_der")])
    plan = provider.plan(inputs)
    assert plan["vista_maestra"] == "trasera_der"
    assert provider.render_frame("trasera_der", plan, inputs, None) == (b"hello", "image/png")
    assert calls[0][1] == {"responseMimeType": "application/json"}
