"""Proveedores de imagen para generar los fotogramas del 360.

DemoImageProvider no llama a ninguna API: reutiliza las fotos del cliente
y les añade una etiqueta, para desarrollar y hacer la demo sin gastar
créditos. GeminiImageProvider usa la API REST de Gemini.
"""
import base64
import io
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, ImageDraw

from ..constants import ANGLES

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DIRECTOR_INSTRUCTIONS = """Eres el director de arte de un taller de modificación estética de coches.
Recibes fotos del coche del cliente (con su ángulo), fotos de las piezas que quiere montar y su petición.
Devuelve SOLO un JSON con: "piezas" (lista de piezas a montar, en inglés), "vista_maestra"
(el ángulo que mejor muestra las modificaciones, uno de: {angles}) y "prompts" (un objeto con
un prompt en inglés por cada ángulo). Cada prompt debe pedir el mismo coche, mismas piezas,
misma cámara a la altura de las ruedas, coche centrado y mismo fondo neutro de estudio."""


class ProviderError(RuntimeError):
    pass


@dataclass
class InputImage:
    data: bytes
    mime: str
    role: str                       # foto_vehiculo | referencia_pieza | version_previa
    angle: Optional[str] = None
    label: Optional[str] = None


@dataclass
class DesignInputs:
    request_text: str
    images: list[InputImage] = field(default_factory=list)

    def vehicle_photo(self, angle: str) -> Optional[InputImage]:
        return next((image for image in self.images if image.role == "foto_vehiculo" and image.angle == angle), None)

    @property
    def vehicle_photos(self) -> list[InputImage]:
        return [image for image in self.images if image.role in ("foto_vehiculo", "version_previa")]

    @property
    def references(self) -> list[InputImage]:
        return [image for image in self.images if image.role == "referencia_pieza"]


class DemoImageProvider:
    name = "demo"
    text_model = "demo"
    image_model = "demo"

    def plan(self, inputs: DesignInputs) -> dict:
        available = [image.angle for image in inputs.vehicle_photos if image.angle]
        rear = [angle for angle in ("trasera_der", "trasera_izq", "trasera") if angle in available]
        master = rear[0] if rear else (available[0] if available else "frontal_izq")
        return {"piezas": [image.label or "pieza" for image in inputs.references], "vista_maestra": master,
                "prompts": {angle: f"[demo] {inputs.request_text}" for angle in ANGLES}}

    def render_frame(self, angle: str, plan: dict, inputs: DesignInputs,
                     master: Optional[InputImage]) -> tuple[bytes, str]:
        source = inputs.vehicle_photo(angle) or master or (inputs.vehicle_photos or [None])[0]
        if source is None:
            raise ProviderError("No hay ninguna foto del vehículo para la demo")
        with Image.open(io.BytesIO(source.data)) as original:
            frame = original.convert("RGB")
        frame.thumbnail((1024, 1024))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 0, frame.width, 34), fill=(17, 17, 17))
        draw.text((12, 10), f"EVOLVE · DEMO · {angle}", fill=(255, 255, 255))
        output = io.BytesIO()
        frame.save(output, format="WEBP", quality=85)
        return output.getvalue(), "image/webp"


class GeminiImageProvider:
    name = "gemini"

    def __init__(self, api_key: str, text_model: str, image_model: str, timeout_seconds: int = 120):
        if not api_key:
            raise ProviderError("Falta GEMINI_API_KEY en la configuración")
        self.api_key = api_key
        self.text_model = text_model
        self.image_model = image_model
        self.timeout_seconds = timeout_seconds

    def _call(self, model: str, parts: list[dict], generation_config: Optional[dict] = None) -> dict:
        body = {"contents": [{"role": "user", "parts": parts}]}
        if generation_config:
            body["generationConfig"] = generation_config
        request = urllib.request.Request(
            GEMINI_ENDPOINT.format(model=model), data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            raise ProviderError(f"Gemini respondió {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderError(f"No se pudo contactar con Gemini: {error}") from error

    @staticmethod
    def _image_part(image: InputImage) -> dict:
        return {"inline_data": {"mime_type": image.mime, "data": base64.b64encode(image.data).decode()}}

    def _labelled_images(self, images: list[InputImage]) -> list[dict]:
        parts = []
        for index, image in enumerate(images, start=1):
            description = (f"vehicle photo, {image.angle} view" if image.role != "referencia_pieza"
                           else f"reference part: {image.label or 'part'}")
            parts += [{"text": f"Image {index}: {description}"}, self._image_part(image)]
        return parts

    def plan(self, inputs: DesignInputs) -> dict:
        parts = [{"text": DIRECTOR_INSTRUCTIONS.format(angles=", ".join(ANGLES))},
                 *self._labelled_images(inputs.images),
                 {"text": f"Petición del cliente: {inputs.request_text}"}]
        response = self._call(self.text_model, parts, {"responseMimeType": "application/json"})
        try:
            text = response["candidates"][0]["content"]["parts"][0]["text"]
            plan = json.loads(text)
        except (KeyError, IndexError, ValueError) as error:
            raise ProviderError("La respuesta del modelo director no es un JSON válido") from error
        if plan.get("vista_maestra") not in ANGLES:
            plan["vista_maestra"] = "trasera_der"
        plan.setdefault("prompts", {})
        return plan

    def render_frame(self, angle: str, plan: dict, inputs: DesignInputs,
                     master: Optional[InputImage]) -> tuple[bytes, str]:
        base_photo = inputs.vehicle_photo(angle)
        images = ([base_photo] if base_photo else []) + inputs.references + ([master] if master else [])
        prompt = plan["prompts"].get(angle) or (
            f"Generate a realistic photo of this exact same modified car seen from the {angle.replace('_', ' ')} "
            "view. Keep the same car, parts, color and wheels. Plain light grey studio background.")
        if master:
            prompt += " The last image shows the approved design: match its parts exactly."
        response = self._call(self.image_model, [*self._labelled_images(images), {"text": prompt}],
                              {"responseModalities": ["IMAGE"]})
        for candidate in response.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                inline = part.get("inline_data") or part.get("inlineData")
                if inline and inline.get("data"):
                    return base64.b64decode(inline["data"]), inline.get("mime_type") or inline.get("mimeType", "image/png")
        raise ProviderError(f"Gemini no devolvió ninguna imagen para {angle}")


def build_image_provider(config: dict, provider_name: str):
    if provider_name == "gemini":
        return GeminiImageProvider(config["GEMINI_API_KEY"], config["GEMINI_TEXT_MODEL"], config["GEMINI_IMAGE_MODEL"])
    return DemoImageProvider()
