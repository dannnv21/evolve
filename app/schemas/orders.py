from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Optional

from pydantic import Field, StringConstraints, field_validator, model_validator

from .common import Angle, Schema, ShortText, TimeSlot

Plate = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=4, max_length=15)]
OrderState = Literal["borrador", "solicitado", "presupuestado", "aceptado", "rechazado", "programado",
                     "en_proceso", "esperando_piezas", "listo", "entregado", "cancelado"]


class VehicleIn(Schema):
    marca: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    modelo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    variante: Optional[Annotated[str, StringConstraints(max_length=60)]] = None
    anio: Optional[int] = Field(default=None, ge=1950, le=2100)
    matricula: Plate
    color: Optional[Annotated[str, StringConstraints(max_length=30)]] = None


class VehicleUpdateIn(Schema):
    marca: Optional[Annotated[str, StringConstraints(min_length=1, max_length=40)]] = None
    modelo: Optional[Annotated[str, StringConstraints(min_length=1, max_length=60)]] = None
    variante: Optional[Annotated[str, StringConstraints(max_length=60)]] = None
    anio: Optional[int] = Field(default=None, ge=1950, le=2100)
    matricula: Optional[Plate] = None
    color: Optional[Annotated[str, StringConstraints(max_length=30)]] = None


class PhotoAngleIn(Schema):
    angulo: Angle


class OrderCreateIn(Schema):
    vehiculo_id: int = Field(gt=0)


class ModificationIn(Schema):
    tipo: Annotated[str, StringConstraints(min_length=1, max_length=30)]
    detalle: Optional[Annotated[str, StringConstraints(max_length=255)]] = None


class OrderDraftUpdateIn(Schema):
    modificaciones: Optional[list[ModificationIn]] = None
    descripcion_cliente: Optional[Annotated[str, StringConstraints(max_length=5000)]] = None
    especificaciones: Optional[dict[str, Any]] = None
    fecha_preferida: Optional[date] = None

    @field_validator("fecha_preferida")
    @classmethod
    def future_date(cls, value: Optional[date]):
        if value is not None and value < date.today():
            raise ValueError("La fecha preferida debe ser futura")
        return value


class ReferenceIn(Schema):
    tipo_modificacion: Annotated[str, StringConstraints(min_length=1, max_length=30)]
    pieza_id: Optional[int] = Field(default=None, gt=0)
    marca: Optional[Annotated[str, StringConstraints(max_length=40)]] = None
    modelo: Optional[Annotated[str, StringConstraints(max_length=80)]] = None
    notas: Optional[Annotated[str, StringConstraints(max_length=255)]] = None


class SubmitOrderIn(Schema):
    version_aprobada_id: Optional[int] = Field(default=None, gt=0)
    franja_entrada: Optional[TimeSlot] = None


class OrderListQuery(Schema):
    estado: Literal["todas", "en_curso", "finalizadas"] = "todas"


class AvailabilitySlotsQuery(Schema):
    date_: date = Field(alias="date")
    duracion_min: int = Field(default=30, ge=15, le=240)

    @field_validator("date_")
    @classmethod
    def future_date(cls, value: date):
        if value < date.today():
            raise ValueError("La fecha debe ser futura")
        return value


class PaymentIn(Schema):
    metodo: Literal["simulado", "tarjeta", "efectivo", "transferencia", "bizum"] = "simulado"
    importe: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


class RatingIn(Schema):
    puntuacion: int = Field(ge=1, le=5)
    comentario: Optional[Annotated[str, StringConstraints(max_length=1000)]] = None


class DesignRequestIn(Schema):
    peticion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]
    version_padre_id: Optional[int] = Field(default=None, gt=0)


class RestoreVersionIn(Schema):
    version_id: int = Field(gt=0)


class ReplicaIn(Schema):
    pedido_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    formato_entrega: Literal["archivo_digital", "impresion_taller"] = "impresion_taller"
    escala: Literal["1:64", "1:43", "1:32", "1:24", "1:18"] = "1:43"
    material: Optional[Literal["PLA", "PETG", "resina"]] = None
    acabado: Optional[Literal["sin_pintar", "pintada"]] = None

    @model_validator(mode="after")
    def printing_needs_material(self):
        if self.formato_entrega == "impresion_taller" and self.material is None:
            raise ValueError("Para imprimirla hay que elegir material")
        return self


class ReplicaStatusIn(Schema):
    estado: Literal["imprimiendo", "lista", "entregada", "cancelada"]
    notas: Optional[ShortText] = None


class AdminOrderCreateIn(Schema):
    cliente_id: int = Field(gt=0)
    vehiculo_id: int = Field(gt=0)
    modificaciones: list[ModificationIn] = Field(min_length=1)
    descripcion_cliente: Optional[str] = None
    entrega_estimada: Optional[datetime] = None
    estado: Literal["solicitado", "aceptado"] = "solicitado"


class AdminOrderUpdateIn(Schema):
    fecha_entrada: Optional[datetime] = None
    entrega_estimada: Optional[datetime] = None
    entrega_real: Optional[datetime] = None
    descripcion_cliente: Optional[str] = None


class OrderStatusIn(Schema):
    estado: OrderState
    progreso: Optional[int] = Field(default=None, ge=0, le=100)
    nota: Optional[Annotated[str, StringConstraints(max_length=255)]] = None
    visible_cliente: bool = True


class AssigneeIn(Schema):
    empleado_id: int = Field(gt=0)


class QuoteLineIn(Schema):
    tipo: Literal["servicio", "pieza", "otro"]
    servicio_id: Optional[int] = Field(default=None, gt=0)
    pieza_id: Optional[int] = Field(default=None, gt=0)
    descripcion: Optional[Annotated[str, StringConstraints(max_length=120)]] = None
    cantidad: Decimal = Field(default=Decimal("1"), gt=0, max_digits=8, decimal_places=2)
    precio_unitario: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)

    @model_validator(mode="after")
    def ids_match_type(self):
        if self.tipo == "servicio" and (self.servicio_id is None or self.pieza_id is not None):
            raise ValueError("Una línea de tipo servicio necesita servicio_id y no pieza_id")
        if self.tipo == "pieza" and (self.pieza_id is None or self.servicio_id is not None):
            raise ValueError("Una línea de tipo pieza necesita pieza_id y no servicio_id")
        if self.tipo == "otro" and (self.descripcion is None or self.precio_unitario is None):
            raise ValueError("Una línea de tipo otro necesita descripción y precio")
        return self


class QuoteIn(Schema):
    lineas: list[QuoteLineIn] = Field(min_length=1)
    iva_pct: Optional[Decimal] = Field(default=None, ge=0, le=100)
    valido_hasta: Optional[date] = None


class AssignPartIn(Schema):
    pieza_id: int = Field(gt=0)
    cantidad: int = Field(default=1, gt=0)


class AdminOrderListQuery(Schema):
    estado: Optional[OrderState] = None
    cliente: Optional[str] = None
    fecha: Optional[date] = None
    responsable_id: Optional[int] = None
