"""Convierte sql/evolve_bbdd.sql (MySQL) en un script equivalente para SQLite.

Solo se usa en los tests: mantiene las tablas, claves, CHECK y datos de
prueba del script real, y sustituye los triggers de MySQL por triggers
SQLite con la misma regla.
"""
import re
from pathlib import Path

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "evolve_bbdd.sql"

SQLITE_TRIGGERS = """
CREATE TRIGGER trg_citas_no_solape_ins BEFORE INSERT ON citas
WHEN NEW.empleado_id IS NOT NULL AND NEW.estado <> 'cancelada'
BEGIN
    SELECT RAISE(ABORT, 'Franja horaria ocupada para este empleado')
     WHERE EXISTS (SELECT 1 FROM citas c WHERE c.empleado_id = NEW.empleado_id AND c.estado <> 'cancelada'
                     AND NEW.inicio < c.fin AND NEW.fin > c.inicio);
END;

CREATE TRIGGER trg_citas_no_solape_upd BEFORE UPDATE ON citas
WHEN NEW.empleado_id IS NOT NULL AND NEW.estado <> 'cancelada'
BEGIN
    SELECT RAISE(ABORT, 'Franja horaria ocupada para este empleado')
     WHERE EXISTS (SELECT 1 FROM citas c WHERE c.empleado_id = NEW.empleado_id AND c.id <> NEW.id
                     AND c.estado <> 'cancelada' AND NEW.inicio < c.fin AND NEW.fin > c.inicio);
END;

CREATE TRIGGER trg_movimientos_stock_bi BEFORE INSERT ON movimientos_stock
BEGIN
    SELECT RAISE(ABORT, 'Stock insuficiente para esta pieza')
     WHERE (SELECT stock FROM piezas WHERE id = NEW.pieza_id) + NEW.cantidad < 0;
END;

CREATE TRIGGER trg_movimientos_stock_ai AFTER INSERT ON movimientos_stock
BEGIN
    UPDATE piezas SET stock = stock + NEW.cantidad WHERE id = NEW.pieza_id;
    UPDATE movimientos_stock SET stock_resultante = (SELECT stock FROM piezas WHERE id = NEW.pieza_id)
     WHERE id = NEW.id;
END;
"""


def _convert_create_table(statement: str) -> str:
    statement = re.sub(r"ENUM\((?:[^()]|\n)*?\)", "TEXT", statement)
    statement = re.sub(r"\b(BIGINT|INT|SMALLINT|TINYINT)\s+UNSIGNED\s+NOT NULL AUTO_INCREMENT",
                       "INTEGER NOT NULL", statement)
    statement = re.sub(r"\s+UNSIGNED", "", statement)
    statement = re.sub(r"\bJSON\b", "TEXT", statement)
    statement = re.sub(r"\s+ON UPDATE CURRENT_TIMESTAMP", "", statement)
    statement = re.sub(r"\)\s*ENGINE=InnoDB", ")", statement)
    statement = re.sub(r"UNIQUE KEY \w+ \(", "UNIQUE (", statement)
    lines = [line for line in statement.split("\n") if not re.match(r"\s*KEY \w+ \(", line)]
    statement = "\n".join(lines)
    return re.sub(r",(\s*\n\s*\))", r"\1", statement)


def sqlite_script() -> str:
    source = SQL_FILE.read_text(encoding="utf-8")
    source = re.sub(r"DELIMITER \$\$.*?DELIMITER ;", "", source, flags=re.S)
    source = "\n".join(line for line in source.split("\n") if not line.lstrip().startswith("--"))
    schema, data = [], []
    for statement in source.split(";\n"):
        stripped = statement.strip()
        if not stripped or re.match(r"(DROP DATABASE|CREATE DATABASE|USE |ALTER TABLE|CREATE VIEW)", stripped):
            continue
        if stripped.startswith("CREATE TABLE"):
            schema.append(_convert_create_table(stripped) + ";")
        else:
            data.append(stripped + ";")
    return "\n\n".join(schema) + "\n" + SQLITE_TRIGGERS + "\n" + "\n\n".join(data)
