"""Almacenamiento de archivos y URLs firmadas.

Los archivos se guardan en disco (STORAGE_DIR/<bucket>/<clave>) y la tabla
`archivos` registra dónde está cada uno. Cambiar a MinIO o R2 solo requiere
otra implementación de FileStorage con los mismos métodos.
"""
import hashlib
import io
import os
import uuid
from typing import Optional

from flask import current_app, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from .db import get_db
from .errors import BadRequest, NotFound

IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class LocalFileStorage:
    backend = "local"

    def __init__(self, root: str):
        self.root = root

    def path(self, bucket: str, key: str) -> str:
        full_path = os.path.normpath(os.path.join(self.root, bucket, key))
        if not full_path.startswith(os.path.normpath(self.root)):
            raise ValueError("Ruta de archivo no válida")
        return full_path

    def save(self, bucket: str, key: str, data: bytes) -> None:
        path = self.path(bucket, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)

    def read(self, bucket: str, key: str) -> bytes:
        with open(self.path(bucket, key), "rb") as handle:
            return handle.read()

    def delete(self, bucket: str, key: str) -> None:
        path = self.path(bucket, key)
        if os.path.exists(path):
            os.remove(path)


def storage() -> LocalFileStorage:
    return LocalFileStorage(current_app.config["STORAGE_DIR"])


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="archivo")


def save_bytes(data: bytes, prefix: str, mime: str, extension: str, uploaded_by: Optional[int] = None,
               original_name: Optional[str] = None, width: Optional[int] = None,
               height: Optional[int] = None) -> int:
    bucket = current_app.config["STORAGE_BUCKET"]
    key = f"{prefix.strip('/')}/{uuid.uuid4().hex}{extension}"
    storage().save(bucket, key, data)
    return get_db().execute(
        """INSERT INTO archivos (almacenamiento, bucket, clave, nombre_original, mime, bytes,
                                 ancho, alto, sha256, subido_por)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (storage().backend, bucket, key, original_name, mime, len(data), width, height,
         hashlib.sha256(data).hexdigest(), uploaded_by))


def save_image_upload(upload: Optional[FileStorage], prefix: str, uploaded_by: int) -> int:
    """Valida que el archivo subido sea una imagen real dentro del límite y lo guarda."""
    if upload is None or not upload.filename:
        raise BadRequest("Falta el archivo", "INVALID_FILE", {"archivo": "Obligatorio"})
    data = upload.read()
    max_bytes = current_app.config["MAX_UPLOAD_MB"] * 1024 * 1024
    if len(data) > max_bytes:
        raise BadRequest(f"Solo se admiten imágenes de hasta {current_app.config['MAX_UPLOAD_MB']} MB", "INVALID_FILE")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
    except (UnidentifiedImageError, OSError) as error:
        raise BadRequest("Solo se admiten imágenes JPG, PNG o WEBP", "INVALID_FILE") from error
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(image_format)
    if mime is None:
        raise BadRequest("Solo se admiten imágenes JPG, PNG o WEBP", "INVALID_FILE")
    return save_bytes(data, prefix, mime, IMAGE_TYPES[mime], uploaded_by, upload.filename, width, height)


def signed_url(file_id: Optional[int]) -> Optional[str]:
    if not file_id:
        return None
    token = _serializer().dumps(int(file_id))
    return url_for("files.download", token=token, _external=True)


def resolve_signed_token(token: str) -> dict:
    try:
        file_id = _serializer().loads(token, max_age=current_app.config["FILE_URL_TTL_SECONDS"])
    except SignatureExpired as error:
        raise NotFound("El enlace al archivo ha caducado", "LINK_EXPIRED") from error
    except BadSignature as error:
        raise NotFound("Archivo no encontrado") from error
    file_row = get_db().fetch_one("SELECT * FROM archivos WHERE id = %s", (file_id,))
    if file_row is None:
        raise NotFound("Archivo no encontrado")
    return file_row


def read_file(file_row: dict) -> bytes:
    return storage().read(file_row["bucket"], file_row["clave"])


def delete_file(file_id: int) -> None:
    file_row = get_db().fetch_one("SELECT bucket, clave FROM archivos WHERE id = %s", (file_id,))
    if file_row:
        get_db().execute("DELETE FROM archivos WHERE id = %s", (file_id,))
        storage().delete(file_row["bucket"], file_row["clave"])
