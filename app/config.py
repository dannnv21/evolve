import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "si", "sí"}


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "cambia-esta-clave-en-produccion")
    DATABASE_URL = os.getenv("DATABASE_URL", "mysql://evolve:evolve@localhost:3306/evolve")

    JWT_SECRET = os.getenv("JWT_SECRET", SECRET_KEY)
    JWT_ACCESS_TTL = timedelta(minutes=int(os.getenv("JWT_ACCESS_MINUTES", "15")))
    REFRESH_TOKEN_TTL = timedelta(days=int(os.getenv("REFRESH_TOKEN_DAYS", "30")))
    RESET_TOKEN_TTL = timedelta(hours=1)

    STORAGE_DIR = os.getenv("STORAGE_DIR", os.path.join(os.getcwd(), "storage"))
    STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "evolve")
    FILE_URL_TTL_SECONDS = int(os.getenv("FILE_URL_TTL_SECONDS", "3600"))
    MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "5"))
    MAX_CONTENT_LENGTH = (MAX_UPLOAD_MB + 1) * 1024 * 1024

    AI_PROVIDER = os.getenv("AI_PROVIDER", "demo")            # demo | gemini
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
    GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    AI_WORKER_POLL_SECONDS = int(os.getenv("AI_WORKER_POLL_SECONDS", "5"))

    WORKSHOP_OPENS_AT = os.getenv("WORKSHOP_OPENS_AT", "09:00")
    WORKSHOP_CLOSES_AT = os.getenv("WORKSHOP_CLOSES_AT", "18:00")
    WORKSHOP_WORKING_DAYS = [int(d) for d in os.getenv("WORKSHOP_WORKING_DAYS", "0,1,2,3,4").split(",")]
    ENTRY_SLOT_MINUTES = int(os.getenv("ENTRY_SLOT_MINUTES", "30"))
    VAT_PERCENT = float(os.getenv("VAT_PERCENT", "21"))
    QUOTE_VALID_DAYS = int(os.getenv("QUOTE_VALID_DAYS", "14"))

    MAIL_SERVER = os.getenv("MAIL_SERVER", "")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", True)
    MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@evolve.test")
    FRONTEND_RESET_URL = os.getenv("FRONTEND_RESET_URL", "evolve://reset-password?token=")
    EXPOSE_RESET_TOKEN = _env_bool("EXPOSE_RESET_TOKEN", False)   # solo desarrollo: devuelve el token en la respuesta

    JSON_SORT_KEYS = False


class TestingConfig(Config):
    TESTING = True
    DATABASE_URL = "sqlite:///:memory:"
    JWT_SECRET = "clave-de-pruebas-con-longitud-suficiente-para-hs256"
    AI_PROVIDER = "demo"
