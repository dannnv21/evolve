import io
import os

import click
from flask import Flask
from PIL import Image, ImageDraw

from .ai.models3d import Demo3DProvider
from .db import get_db
from .storage import storage

PLACEHOLDER_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def _placeholder_image(label: str, mime: str) -> bytes:
    image = Image.new("RGB", (1024, 1024), (38, 40, 44))
    draw = ImageDraw.Draw(image)
    draw.rectangle((180, 520, 844, 700), fill=(170, 172, 178))
    draw.rectangle((330, 410, 700, 530), fill=(140, 142, 150))
    draw.ellipse((250, 650, 390, 790), fill=(20, 20, 20))
    draw.ellipse((634, 650, 774, 790), fill=(20, 20, 20))
    draw.text((40, 40), f"EVOLVE · {label}", fill=(255, 255, 255))
    output = io.BytesIO()
    image.save(output, format=PLACEHOLDER_FORMATS[mime])
    return output.getvalue()


def register_commands(app: Flask) -> None:
    @app.cli.command("ai-worker")
    @click.option("--once", is_flag=True, help="Procesa los trabajos pendientes y termina.")
    def ai_worker_command(once: bool):
        """Procesa la cola de trabajos de IA (360 y réplicas 3D)."""
        from .ai.worker import run_worker
        run_worker(once=once)

    @app.cli.command("seed-storage")
    def seed_storage_command():
        """Crea archivos de ejemplo para las filas de `archivos` de los datos de prueba que no existen en disco."""
        file_storage = storage()
        demo_model = None
        created = 0
        for row in get_db().fetch_all("SELECT id, bucket, clave, mime FROM archivos"):
            if os.path.exists(file_storage.path(row["bucket"], row["clave"])):
                continue
            if row["mime"] in PLACEHOLDER_FORMATS:
                data = _placeholder_image(row["clave"].rsplit("/", 1)[-1], row["mime"])
            else:
                demo_model = demo_model or Demo3DProvider().generate([_placeholder_image("replica", "image/png")], "1:43")
                data = demo_model.glb if row["mime"] == "model/gltf-binary" else demo_model.stl
            file_storage.save(row["bucket"], row["clave"], data)
            created += 1
        click.echo(f"Archivos de ejemplo creados: {created}")
