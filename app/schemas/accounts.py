from typing import Annotated, Literal, Optional

from pydantic import Field, StringConstraints, model_validator

from .common import Dni, Email, Name, Password, Phone, Role, Schema, Surname

NotificationType = Literal["estado_pedido", "presupuesto", "diseno_listo", "replica",
                           "solicitud_nueva", "stock_bajo", "entrega_hoy", "sistema"]
Username = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True,
                                            pattern=r"^[a-z0-9_.]{3,30}$")]


class RegisterIn(Schema):
    nombre: Name
    apellidos: Surname
    email: Email
    password: Password
    password_repetida: str
    telefono: Optional[Phone] = None

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.password_repetida:
            raise ValueError("Las contraseñas no coinciden")
        return self


class LoginIn(Schema):
    email: Email
    password: str = Field(min_length=1)


class RefreshIn(Schema):
    refresh_token: str = Field(min_length=10)


class LogoutIn(Schema):
    refresh_token: Optional[str] = None


class ForgotPasswordIn(Schema):
    email: Email


class ResetPasswordIn(Schema):
    token: str = Field(min_length=10)
    password: Password
    password_repetida: str

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.password_repetida:
            raise ValueError("Las contraseñas no coinciden")
        return self


class ProfileUpdateIn(Schema):
    nombre: Optional[Name] = None
    apellidos: Optional[Surname] = None
    email: Optional[Email] = None
    telefono: Optional[Phone] = None
    dni: Optional[Dni] = None


class PasswordChangeIn(Schema):
    password_actual: str = Field(min_length=1)
    password_nueva: Password
    password_repetida: str

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password_nueva != self.password_repetida:
            raise ValueError("Las contraseñas no coinciden")
        return self


class NotificationPreferenceIn(Schema):
    tipo: NotificationType
    push: bool = True
    email: bool = False


class NotificationPreferencesIn(Schema):
    preferencias: list[NotificationPreferenceIn] = Field(min_length=1)


class DeviceIn(Schema):
    token_push: str = Field(min_length=10, max_length=255)
    plataforma: Literal["android", "ios", "web"] = "android"


class UserCreateIn(Schema):
    nombre: Name
    apellidos: Surname
    email: Email
    dni: Optional[Dni] = None
    telefono: Optional[Phone] = None
    nombre_usuario: Optional[Username] = None
    rol: Role
    activo: bool = True
    password_temporal: Password


class UserUpdateIn(Schema):
    nombre: Optional[Name] = None
    apellidos: Optional[Surname] = None
    email: Optional[Email] = None
    dni: Optional[Dni] = None
    telefono: Optional[Phone] = None
    nombre_usuario: Optional[Username] = None
    rol: Optional[Role] = None


class UserStatusIn(Schema):
    activo: bool


class UserListQuery(Schema):
    rol: Optional[Role] = None
    activo: Optional[bool] = None
    q: Optional[str] = None
