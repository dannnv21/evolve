"""Acceso a la base de datos con SQL parametrizado.

En producción se usa MySQL (PyMySQL). Los tests usan SQLite con el mismo
esquema; esta capa adapta los marcadores de parámetros y las cláusulas de
bloqueo para que las consultas de la aplicación sean las mismas.
"""
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Optional
from urllib.parse import unquote, urlparse

from flask import current_app, g

_LOCK_CLAUSE = re.compile(r"\s+FOR UPDATE(\s+SKIP LOCKED)?", re.IGNORECASE)


class DriverError(Exception):
    """Error del motor de base de datos con su mensaje original."""

    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind            # integrity | signal | other


def _sqlite_datetime(value: bytes):
    text = value.decode()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return text


sqlite3.register_converter("DATETIME", _sqlite_datetime)
sqlite3.register_converter("DATE", lambda v: date.fromisoformat(v.decode()))
sqlite3.register_adapter(datetime, lambda v: v.strftime("%Y-%m-%d %H:%M:%S"))
sqlite3.register_adapter(date, lambda v: v.isoformat())
sqlite3.register_adapter(Decimal, str)


class Database:
    def __init__(self, url: str):
        self.url = url
        self.is_sqlite = url.startswith("sqlite")
        self._conn = None

    def connect(self):
        if self._conn is not None:
            return self._conn
        if self.is_sqlite:
            path = self.url.replace("sqlite:///", "", 1) or ":memory:"
            self._conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        else:
            import pymysql

            parsed = urlparse(self.url)
            self._conn = pymysql.connect(
                host=parsed.hostname or "localhost",
                port=parsed.port or 3306,
                user=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
                database=parsed.path.lstrip("/"),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False,
            )
        return self._conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.rollback()
            finally:
                self._conn.close()
                self._conn = None

    def _prepare(self, sql: str) -> str:
        if self.is_sqlite:
            return _LOCK_CLAUSE.sub("", sql).replace("%s", "?")
        return sql

    def _run(self, sql: str, params: Iterable[Any]):
        cursor = self.connect().cursor()
        try:
            cursor.execute(self._prepare(sql), tuple(params))
        except Exception as error:   # noqa: BLE001 - se traduce a un error propio
            raise _translate(error) from error
        return cursor

    def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        cursor = self._run(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        cursor = self._run(sql, params)
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    def fetch_value(self, sql: str, params: Iterable[Any] = ()):
        row = self.fetch_one(sql, params)
        return next(iter(row.values())) if row else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Ejecuta una sentencia y devuelve el id insertado (o 0)."""
        cursor = self._run(sql, params)
        return cursor.lastrowid or 0

    def execute_rowcount(self, sql: str, params: Iterable[Any] = ()) -> int:
        return self._run(sql, params).rowcount

    def commit(self):
        self.connect().commit()

    def rollback(self):
        self.connect().rollback()

    @contextmanager
    def transaction(self):
        try:
            yield self
            self.commit()
        except Exception:
            self.rollback()
            raise


_TRIGGER_MESSAGES = ("Stock insuficiente", "Franja horaria", "La pieza no existe")


def _translate(error: Exception) -> Exception:
    """Convierte los errores de trigger y de integridad en DriverError; el resto se propaga."""
    args = getattr(error, "args", ()) or ()
    message = str(args[-1]) if args else str(error)
    if any(text in message for text in _TRIGGER_MESSAGES) or (args and args[0] == 1644):
        return DriverError(message, "signal")
    if type(error).__name__ == "IntegrityError":
        return DriverError(message, "integrity")
    return error


def get_db() -> Database:
    if "db" not in g:
        g.db = Database(current_app.config["DATABASE_URL"])
    return g.db


def close_db(_exception=None):
    database = g.pop("db", None)
    if database is not None:
        database.close()


def placeholders(count: int) -> str:
    return ", ".join(["%s"] * count)
