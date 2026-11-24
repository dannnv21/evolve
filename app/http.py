"""Utilidades comunes de petición y respuesta."""
import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional, Type, TypeVar

from flask import jsonify, request
from flask.json.provider import DefaultJSONProvider
from pydantic import BaseModel

from .errors import BadRequest

ModelT = TypeVar("ModelT", bound=BaseModel)
MAX_PAGE_SIZE = 100


class ApiJSONProvider(DefaultJSONProvider):
    ensure_ascii = False
    sort_keys = False

    @staticmethod
    def default(value: Any):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, datetime):
            return value.replace(microsecond=0).isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return DefaultJSONProvider.default(value)


def parse_body(model: Type[ModelT]) -> ModelT:
    payload = request.get_json(silent=True)
    if payload is None:
        if request.data:
            raise BadRequest("El body no es un JSON válido", "INVALID_JSON")
        payload = {}
    return model.model_validate(payload)


def parse_query(model: Type[ModelT]) -> ModelT:
    values = {key: value for key, value in request.args.items() if value != ""}
    return model.model_validate(values)


def pagination() -> tuple[int, int, int]:
    try:
        page = max(int(request.args.get("page", 1)), 1)
        limit = min(max(int(request.args.get("limit", 20)), 1), MAX_PAGE_SIZE)
    except ValueError as error:
        raise BadRequest("page y limit deben ser números") from error
    return page, limit, (page - 1) * limit


def paginated(rows: list, total: int, page: int):
    return jsonify({"data": rows, "total": total, "page": page})


def no_content():
    return "", 204


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def to_json_text(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def as_datetime(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def as_date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value.date() if isinstance(value, datetime) else value
    return date.fromisoformat(str(value)[:10])


def order_code(order_id: int) -> str:
    return f"EV-{order_id:04d}"


def purchase_code(purchase_id: int) -> str:
    return f"PO-{purchase_id:04d}"


def now() -> datetime:
    return datetime.now().replace(microsecond=0)
