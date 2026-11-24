import io

from flask import Blueprint, send_file

from ..storage import read_file, resolve_signed_token

bp = Blueprint("files", __name__, url_prefix="/files")


@bp.get("/<token>")
def download(token: str):
    """Sirve un archivo a través de una URL firmada y temporal."""
    file_row = resolve_signed_token(token)
    return send_file(io.BytesIO(read_file(file_row)), mimetype=file_row["mime"],
                     download_name=file_row["nombre_original"] or file_row["clave"].rsplit("/", 1)[-1],
                     max_age=300)
