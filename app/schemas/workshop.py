from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Optional

from pydantic import Field, StringConstraints, model_validator

from .common import Dni, Email, Name, Password, Phone, Schema, ShortText, StaffRole, Surname, TimeSlot

Availability = Literal["disponible", "ausente", "vacaciones", "baja"]
PurchaseState = Literal["borrador", "pedido", "en_transito", "recibido", "cancelado"]


class CalendarQuery(Schema):
    from_: Optional[date] = Field(default=None, alias="from")
    to: Optional[date] = None
    empleado_id: Optional[int] = None
    cliente: Optional[str] = None

    @model_validator(mode="after")
    def check_order(self):
        if self.from_ and self.to and self.to < self.from_:
            raise ValueError("`to` debe ser posterior a `from`")
        return self


class SlotCheckQuery(TimeSlot):
    empleado_id: int = Field(gt=0)


class EventIn(TimeSlot):
    tipo: Literal["entrada", "trabajo", "revision", "entrega", "otro"]
    pedido_id: Optional[int] = Field(default=None, gt=0)
    empleado_id: Optional[int] = Field(default=None, gt=0)
    titulo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    descripcion: Optional[Annotated[str, StringConstraints(max_length=255)]] = None


class EventUpdateIn(Schema):
    tipo: Optional[Literal["entrada", "trabajo", "revision", "entrega", "otro"]] = None
    empleado_id: Optional[int] = Field(default=None, gt=0)
    titulo: Optional[Annotated[str, StringConstraints(min_length=1, max_length=80)]] = None
    descripcion: Optional[Annotated[str, StringConstraints(max_length=255)]] = None
    inicio: Optional[datetime] = None
    fin: Optional[datetime] = None
    estado: Optional[Literal["programada", "completada", "cancelada"]] = None


class ServiceIn(Schema):
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    tipo_modificacion_id: Optional[int] = Field(default=None, gt=0)
    descripcion: Optional[Annotated[str, StringConstraints(max_length=255)]] = None
    precio_base: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    horas_estimadas: Decimal = Field(default=Decimal("1"), gt=0, max_digits=5, decimal_places=1)


class ServiceUpdateIn(Schema):
    nombre: Optional[Annotated[str, StringConstraints(min_length=2, max_length=80)]] = None
    tipo_modificacion_id: Optional[int] = Field(default=None, gt=0)
    descripcion: Optional[Annotated[str, StringConstraints(max_length=255)]] = None
    precio_base: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    horas_estimadas: Optional[Decimal] = Field(default=None, gt=0, max_digits=5, decimal_places=1)


class ActiveFlagIn(Schema):
    activo: bool


class EmployeeCreateIn(Schema):
    nombre: Name
    apellidos: Surname
    email: Email
    dni: Optional[Dni] = None
    telefono: Optional[Phone] = None
    rol: StaffRole = "empleado"
    puesto_id: int = Field(gt=0)
    especialidad: Optional[Annotated[str, StringConstraints(max_length=60)]] = None
    capacidad_semanal: int = Field(default=5, ge=1, le=50)
    fecha_alta: date
    password_temporal: Password


class EmployeeUpdateIn(Schema):
    puesto_id: Optional[int] = Field(default=None, gt=0)
    especialidad: Optional[Annotated[str, StringConstraints(max_length=60)]] = None
    capacidad_semanal: Optional[int] = Field(default=None, ge=1, le=50)
    rol: Optional[StaffRole] = None


class AvailabilityIn(Schema):
    disponibilidad: Availability
    ausente_hasta: Optional[date] = None

    @model_validator(mode="after")
    def return_date_required(self):
        if self.disponibilidad in ("ausente", "vacaciones") and self.ausente_hasta is None:
            raise ValueError("Indica hasta cuándo está ausente")
        return self


class PartIn(Schema):
    referencia: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=2, max_length=30)]
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    descripcion: Optional[Annotated[str, StringConstraints(max_length=255)]] = None
    marca: Optional[Annotated[str, StringConstraints(max_length=40)]] = None
    categoria_id: int = Field(gt=0)
    proveedor_id: Optional[int] = Field(default=None, gt=0)
    precio_venta: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    coste: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    stock: int = Field(default=0, ge=0)
    stock_minimo: int = Field(default=0, ge=0)
    ubicacion: Optional[Annotated[str, StringConstraints(max_length=40)]] = None


class PartUpdateIn(Schema):
    nombre: Optional[Annotated[str, StringConstraints(min_length=2, max_length=80)]] = None
    descripcion: Optional[Annotated[str, StringConstraints(max_length=255)]] = None
    marca: Optional[Annotated[str, StringConstraints(max_length=40)]] = None
    categoria_id: Optional[int] = Field(default=None, gt=0)
    proveedor_id: Optional[int] = Field(default=None, gt=0)
    precio_venta: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    coste: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    stock_minimo: Optional[int] = Field(default=None, ge=0)
    ubicacion: Optional[Annotated[str, StringConstraints(max_length=40)]] = None


class StockAdjustmentIn(Schema):
    cantidad: int
    motivo: ShortText

    @model_validator(mode="after")
    def non_zero(self):
        if self.cantidad == 0:
            raise ValueError("La cantidad no puede ser 0")
        return self


class PartListQuery(Schema):
    stock: Optional[Literal["critico", "bajo", "correcto"]] = None
    categoria_id: Optional[int] = None
    q: Optional[str] = None


class SupplierIn(Schema):
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    cif: Optional[Annotated[str, StringConstraints(max_length=15)]] = None
    email: Optional[Email] = None
    telefono: Optional[Phone] = None
    web: Optional[Annotated[str, StringConstraints(max_length=255)]] = None


class PurchaseLineIn(Schema):
    pieza_id: int = Field(gt=0)
    cantidad: int = Field(gt=0)
    coste_unitario: Decimal = Field(ge=0, max_digits=10, decimal_places=2)


class PurchaseIn(Schema):
    proveedor_id: int = Field(gt=0)
    entrega_prevista: Optional[date] = None
    estado: Literal["borrador", "pedido"] = "pedido"
    notas: Optional[Annotated[str, StringConstraints(max_length=255)]] = None
    lineas: list[PurchaseLineIn] = Field(min_length=1)


class PurchaseUpdateIn(Schema):
    estado: Optional[Literal["pedido", "en_transito"]] = None
    entrega_prevista: Optional[date] = None
    notas: Optional[Annotated[str, StringConstraints(max_length=255)]] = None


class ReceivedLineIn(Schema):
    pieza_id: int = Field(gt=0)
    cantidad_recibida: int = Field(ge=0)


class ReceivePurchaseIn(Schema):
    lineas: Optional[list[ReceivedLineIn]] = None


class ReportIn(Schema):
    tipo: Literal["rendimiento", "facturacion", "almacen", "valoraciones"]
    formato: Literal["csv", "pdf"] = "csv"
    periodo_inicio: date
    periodo_fin: date
    nombre: ShortText

    @model_validator(mode="after")
    def check_period(self):
        if self.periodo_fin < self.periodo_inicio:
            raise ValueError("El fin del periodo es anterior al inicio")
        return self


class GeneralSettingsIn(Schema):
    nombre: Optional[ShortText] = None
    email: Optional[Email] = None
    telefono: Optional[Phone] = None
    horario: Optional[ShortText] = None
    direccion: Optional[ShortText] = None
    coordenadas: Optional[dict[str, float]] = None


class BusinessSettingsIn(Schema):
    razon_social: Optional[ShortText] = None
    nif: Optional[Annotated[str, StringConstraints(max_length=15)]] = None
    capacidad_maxima: Optional[int] = Field(default=None, ge=1, le=200)
    plazo_medio_dias: Optional[int] = Field(default=None, ge=1, le=120)
    direccion_fiscal: Optional[ShortText] = None


class AutomaticNoticesIn(Schema):
    cambios_estado: Optional[bool] = None
    confirmacion_presupuestos: Optional[bool] = None
    alertas_almacen: Optional[bool] = None
    resumen_diario: Optional[bool] = None


PermissionLevel = Literal["ninguno", "ver", "editar"]


class RolePermissionsIn(Schema):
    rol: Literal["jefe_taller", "empleado"]
    pedidos: Optional[PermissionLevel] = None
    calendario: Optional[PermissionLevel] = None
    almacen: Optional[PermissionLevel] = None
    cuentas: Optional[PermissionLevel] = None
    equipo: Optional[PermissionLevel] = None
    informes: Optional[PermissionLevel] = None
    configuracion: Optional[PermissionLevel] = None


class PermissionsIn(Schema):
    roles: list[RolePermissionsIn] = Field(min_length=1)


def changed_fields(model: Schema) -> dict[str, Any]:
    return model.model_dump(exclude_unset=True, by_alias=False)
