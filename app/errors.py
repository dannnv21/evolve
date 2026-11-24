import logging
from typing import Optional

from flask import Flask, jsonify
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from .db import DriverError

logger = logging.getLogger(__name__)


class ApiError(Exception):
    status = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, code: Optional[str] = None, fields: Optional[dict] = None,
                 status: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.fields = fields
        self.status = status or self.status

    def to_response(self):
        body = {"error": {"code": self.code, "message": self.message}}
        if self.fields:
            body["error"]["fields"] = self.fields
        return jsonify(body), self.status


class BadRequest(ApiError):
    status, code = 400, "VALIDATION_ERROR"


class Unauthorized(ApiError):
    status, code = 401, "UNAUTHORIZED"


class Forbidden(ApiError):
    status, code = 403, "FORBIDDEN"


class NotFound(ApiError):
    status, code = 404, "NOT_FOUND"


class Conflict(ApiError):
    status, code = 409, "CONFLICT"


def validation_fields(error: ValidationError) -> dict:
    fields = {}
    for issue in error.errors():
        location = ".".join(str(part) for part in issue["loc"]) or "body"
        message = issue["msg"].removeprefix("Value error, ")
        fields[location] = message
    return fields


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return error.to_response()

    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        return BadRequest("Revisa los campos marcados", fields=validation_fields(error)).to_response()

    @app.errorhandler(DriverError)
    def handle_driver_error(error: DriverError):
        message = str(error)
        if "Franja horaria" in message:
            return Conflict("Franja horaria ocupada para este empleado", "SLOT_TAKEN").to_response()
        if "Stock insuficiente" in message:
            return Conflict("Stock insuficiente para esta pieza", "INSUFFICIENT_STOCK").to_response()
        if error.kind == "integrity":
            logger.warning("Violación de integridad: %s", message)
            return Conflict("La operación entra en conflicto con datos existentes", "INTEGRITY_ERROR").to_response()
        logger.exception("Error de base de datos")
        return ApiError("Error interno", "INTERNAL_ERROR", status=500).to_response()

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException):
        codes = {404: "ROUTE_NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "FILE_TOO_LARGE"}
        messages = {404: "La ruta no existe", 405: "Método no permitido en esta ruta",
                    413: "El archivo supera el tamaño máximo"}
        status = error.code or 500
        return ApiError(messages.get(status, error.description or "Error"),
                        codes.get(status, "HTTP_ERROR"), status=status).to_response()

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception):
        logger.exception("Error no controlado")
        return ApiError("Error interno del servidor", "INTERNAL_ERROR", status=500).to_response()
