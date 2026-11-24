from flask import Blueprint, current_app, jsonify

from ...db import get_db
from ...errors import BadRequest, Unauthorized
from ...http import no_content, now, parse_body
from ...schemas.accounts import ForgotPasswordIn, LoginIn, LogoutIn, RefreshIn, RegisterIn, ResetPasswordIn
from ...security import (create_access_token, current_user, hash_password, hash_token, login_required,
                         new_opaque_token, verify_password)
from ...services.accounts import ensure_unique_identity, find_user, generate_username, role_id, serialize_user
from ...services.mailer import send_email

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _issue_tokens(user: dict) -> dict:
    refresh_token = new_opaque_token()
    get_db().execute(
        "INSERT INTO tokens_usuario (usuario_id, tipo, token_hash, expira_en) VALUES (%s, 'refresh', %s, %s)",
        (user["id"], hash_token(refresh_token), now() + current_app.config["REFRESH_TOKEN_TTL"]))
    return {"access_token": create_access_token(user["id"], user["rol"]), "token_type": "Bearer",
            "expires_in": int(current_app.config["JWT_ACCESS_TTL"].total_seconds()),
            "refresh_token": refresh_token}


@bp.post("/register")
def register():
    data = parse_body(RegisterIn)
    db = get_db()
    with db.transaction():
        ensure_unique_identity(email=data.email)
        user_id = db.execute(
            """INSERT INTO usuarios (rol_id, nombre_usuario, nombre, apellidos, email, password_hash, telefono)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (role_id("cliente"), generate_username(data.nombre, data.apellidos), data.nombre, data.apellidos,
             data.email, hash_password(data.password), data.telefono))
    user = find_user(user_id)
    return jsonify({"id": user["id"], "rol": user["rol"], "nombre_usuario": user["nombre_usuario"],
                    "email": user["email"], "creado_en": user["creado_en"]}), 201


@bp.post("/login")
def login():
    data = parse_body(LoginIn)
    db = get_db()
    account = db.fetch_one(
        """SELECT u.id, u.password_hash, r.codigo AS rol FROM usuarios u JOIN roles r ON r.id = u.rol_id
            WHERE u.email = %s AND u.activo = TRUE AND u.eliminado_en IS NULL""", (data.email,))
    if account is None or not verify_password(account["password_hash"], data.password):
        raise Unauthorized("Email o contraseña incorrectos", "INVALID_CREDENTIALS")
    with db.transaction():
        db.execute("UPDATE usuarios SET ultimo_acceso = %s WHERE id = %s", (now(), account["id"]))
        tokens = _issue_tokens(account)
    return jsonify({**tokens, "usuario": serialize_user(find_user(account["id"]))})


@bp.post("/refresh")
def refresh():
    data = parse_body(RefreshIn)
    token_row = get_db().fetch_one(
        """SELECT t.usuario_id, r.codigo AS rol FROM tokens_usuario t
             JOIN usuarios u ON u.id = t.usuario_id JOIN roles r ON r.id = u.rol_id
            WHERE t.token_hash = %s AND t.tipo = 'refresh' AND t.usado_en IS NULL AND t.expira_en > %s
              AND u.activo = TRUE""", (hash_token(data.refresh_token), now()))
    if token_row is None:
        raise Unauthorized("El token de refresco no es válido o ha caducado", "INVALID_REFRESH")
    return jsonify({"access_token": create_access_token(token_row["usuario_id"], token_row["rol"]),
                    "token_type": "Bearer",
                    "expires_in": int(current_app.config["JWT_ACCESS_TTL"].total_seconds())})


@bp.get("/me")
@login_required
def me():
    return jsonify(serialize_user(find_user(current_user()["id"])))


@bp.post("/forgot-password")
def forgot_password():
    data = parse_body(ForgotPasswordIn)
    db = get_db()
    user = db.fetch_one("SELECT id, nombre FROM usuarios WHERE email = %s AND activo = TRUE", (data.email,))
    response = {"mensaje": "Si el email existe, recibirás un enlace en unos minutos"}
    if user:
        reset_token = new_opaque_token()
        with db.transaction():
            db.execute(
                "INSERT INTO tokens_usuario (usuario_id, tipo, token_hash, expira_en) VALUES (%s, 'reset_password', %s, %s)",
                (user["id"], hash_token(reset_token), now() + current_app.config["RESET_TOKEN_TTL"]))
        send_email(data.email, "Restablece tu contraseña de EVOLVE",
                   f"Hola {user['nombre']},\n\nUsa este enlace en la próxima hora:\n"
                   f"{current_app.config['FRONTEND_RESET_URL']}{reset_token}\n")
        if current_app.config["EXPOSE_RESET_TOKEN"]:
            response["token_desarrollo"] = reset_token
    return jsonify(response)


@bp.post("/reset-password")
def reset_password():
    data = parse_body(ResetPasswordIn)
    db = get_db()
    token_row = db.fetch_one(
        """SELECT id, usuario_id FROM tokens_usuario
            WHERE token_hash = %s AND tipo = 'reset_password' AND usado_en IS NULL AND expira_en > %s""",
        (hash_token(data.token), now()))
    if token_row is None:
        raise BadRequest("El enlace ha caducado, solicita otro", "TOKEN_EXPIRED")
    with db.transaction():
        db.execute("UPDATE usuarios SET password_hash = %s WHERE id = %s",
                   (hash_password(data.password), token_row["usuario_id"]))
        db.execute("UPDATE tokens_usuario SET usado_en = %s WHERE usuario_id = %s AND usado_en IS NULL",
                   (now(), token_row["usuario_id"]))
    return jsonify({"mensaje": "Contraseña actualizada"})


@bp.post("/logout")
@login_required
def logout():
    data = parse_body(LogoutIn)
    if data.refresh_token:
        db = get_db()
        with db.transaction():
            db.execute("UPDATE tokens_usuario SET usado_en = %s WHERE token_hash = %s AND usuario_id = %s",
                       (now(), hash_token(data.refresh_token), current_user()["id"]))
    return no_content()
