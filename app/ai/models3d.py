"""Generación del modelo 3D de la réplica en miniatura.

Demo3DProvider genera un modelo simplificado (carrocería y cabina) en GLB y
STL para probar el flujo completo sin GPU. Un proveedor real (TRELLIS en
Hugging Face) debe exponer el mismo método `generate`.
"""
import io
import json
import struct
from dataclasses import dataclass

from PIL import Image, ImageDraw

SCALE_FACTORS = {"1:64": 1 / 64, "1:43": 1 / 43, "1:32": 1 / 32, "1:24": 1 / 24, "1:18": 1 / 18}
CAR_SIZE_MM = (4500.0, 1800.0, 1400.0)


@dataclass
class ModelFiles:
    glb: bytes
    stl: bytes
    preview: bytes


def _box(x0, y0, z0, x1, y1, z1) -> tuple[list[tuple], list[tuple]]:
    vertices = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (2, 3, 7), (2, 7, 6), (1, 2, 6), (1, 6, 5), (3, 0, 4), (3, 4, 7)]
    return vertices, faces


def _car_mesh(scale: float) -> tuple[list[tuple], list[tuple]]:
    length, width, height = (size * scale for size in CAR_SIZE_MM)
    body = _box(0, 0, 0, length, width, height * 0.55)
    cabin = _box(length * 0.28, width * 0.08, height * 0.55, length * 0.75, width * 0.92, height)
    vertices = body[0] + cabin[0]
    faces = body[1] + [(a + 8, b + 8, c + 8) for a, b, c in cabin[1]]
    return vertices, faces


def _stl(vertices, faces, name: str) -> bytes:
    lines = [f"solid {name}"]
    for a, b, c in faces:
        lines += ["  facet normal 0 0 0", "    outer loop"]
        lines += [f"      vertex {vertices[i][0]:.3f} {vertices[i][1]:.3f} {vertices[i][2]:.3f}" for i in (a, b, c)]
        lines += ["    endloop", "  endfacet"]
    lines.append(f"endsolid {name}")
    return "\n".join(lines).encode()


def _glb(vertices, faces) -> bytes:
    positions = b"".join(struct.pack("<3f", x / 1000, z / 1000, y / 1000) for x, y, z in vertices)
    indices = b"".join(struct.pack("<3H", *face) for face in faces)
    indices += b"\x00" * (-len(indices) % 4)
    binary = positions + indices
    minimum = [min(v[i] for v in vertices) / 1000 for i in (0, 2, 1)]
    maximum = [max(v[i] for v in vertices) / 1000 for i in (0, 2, 1)]
    document = {
        "asset": {"version": "2.0", "generator": "EVOLVE demo"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "replica"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.75, 0.75, 0.78, 1], "metallicFactor": 0.6}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
                        {"buffer": 0, "byteOffset": len(positions), "byteLength": len(faces) * 6, "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(vertices), "type": "VEC3",
                       "min": minimum, "max": maximum},
                      {"bufferView": 1, "componentType": 5123, "count": len(faces) * 3, "type": "SCALAR"}],
    }
    json_chunk = json.dumps(document, separators=(",", ":")).encode()
    json_chunk += b" " * (-len(json_chunk) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(json_chunk), b"JSON") + json_chunk
            + struct.pack("<I4s", len(binary), b"BIN\x00") + binary)


def _preview(master_frame: bytes, scale: str) -> bytes:
    with Image.open(io.BytesIO(master_frame)) as original:
        image = original.convert("RGB")
    image.thumbnail((768, 768))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, image.height - 34, image.width, image.height), fill=(17, 17, 17))
    draw.text((12, image.height - 24), f"Réplica {scale} · modelo demo", fill=(255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=85)
    return output.getvalue()


class Demo3DProvider:
    name = "demo"

    def generate(self, frames: list[bytes], scale: str) -> ModelFiles:
        if not frames:
            raise ValueError("La versión no tiene fotogramas")
        vertices, faces = _car_mesh(SCALE_FACTORS.get(scale, 1 / 43))
        return ModelFiles(glb=_glb(vertices, faces), stl=_stl(vertices, faces, "evolve_replica"),
                          preview=_preview(frames[0], scale))
