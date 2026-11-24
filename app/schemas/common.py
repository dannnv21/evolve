import re
from datetime import date, datetime
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DNI_PATTERN = re.compile(r"^\d{8}[A-Za-z]$")


def _email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL_PATTERN.match(value):
        raise ValueError("Email no válido")
    return value


def _password(value: str) -> str:
    if len(value) < 8 or not re.search(r"[A-Z]", value) or not re.search(r"\d", value):
        raise ValueError("Mínimo 8 caracteres, una mayúscula y un número")
    return value


def _dni(value: str) -> str:
    value = value.strip().upper()
    if not DNI_PATTERN.match(value):
        raise ValueError("Debe tener 8 números y una letra")
    return value


Email = Annotated[str, AfterValidator(_email)]
Password = Annotated[str, AfterValidator(_password)]
Dni = Annotated[str, AfterValidator(_dni)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Surname = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=20)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]

Angle = Literal["frontal", "frontal_izq", "lateral_izq", "trasera_izq",
                "trasera", "trasera_der", "lateral_der", "frontal_der"]
Role = Literal["admin", "jefe_taller", "empleado", "cliente"]
StaffRole = Literal["admin", "jefe_taller", "empleado"]


class Schema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class DateRangeQuery(Schema):
    from_: date = Field(alias="from")
    to: date

    @model_validator(mode="after")
    def check_order(self):
        if self.to < self.from_:
            raise ValueError("`to` debe ser posterior a `from`")
        if (self.to - self.from_).days > 62:
            raise ValueError("El rango máximo es de 62 días")
        return self


class TimeSlot(Schema):
    inicio: datetime
    fin: datetime

    @model_validator(mode="after")
    def check_order(self):
        if self.fin <= self.inicio:
            raise ValueError("La hora de fin debe ser posterior a la de inicio")
        return self


class OptionalReason(Schema):
    motivo: Optional[Annotated[str, StringConstraints(max_length=255)]] = None


class RequiredReason(Schema):
    motivo: ShortText
