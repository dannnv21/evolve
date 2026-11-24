# EVOLVE API

API REST de **EVOLVE · Rebuild your Ride**, hecha con Flask. Da servicio a la app Android de clientes y al panel de escritorio del taller. Cubre:

- cuentas y autenticación;
- vehículos y pedidos;
- citas, con la regla que impide reservar una franja ya ocupada;
- presupuestos y pagos;
- diseños 360 generados con IA;
- réplicas 3D;
- almacén, compras, empleados, informes y ajustes.

- Prefijo de todas las rutas: `/api/v1`
- 133 endpoints, los mismos de la colección de Postman
- Base de datos: MySQL 8 (o MariaDB 10.5+) con el esquema `sql/evolve_bbdd.sql`

## Requisitos

- Python 3.11 o superior
- MySQL 8.0.16+ o MariaDB 10.5+ (por ejemplo con XAMPP, Laragon o MySQL Workbench)

## Puesta en marcha

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    ·    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # en Windows: copy .env.example .env
```

Edita `.env` y ajusta `DATABASE_URL` con tu usuario y contraseña de MySQL (`mysql://usuario:contraseña@host:puerto/evolve`).

### 1. Crear la base de datos

El script crea la base `evolve` (borra la anterior si existe), las tablas, vistas y triggers, y carga los datos de prueba:

```bash
mysql -u root -p < sql/evolve_bbdd.sql
```

En MySQL Workbench: *File → Open SQL Script* y ejecuta el script entero (rayo).

Si en `.env` usas un usuario distinto de `root`, dale permisos:

```sql
CREATE USER 'evolve'@'localhost' IDENTIFIED BY 'evolve';
GRANT ALL PRIVILEGES ON evolve.* TO 'evolve'@'localhost';
```

### 2. Crear los archivos de ejemplo

Los datos de prueba hacen referencia a fotos y modelos 3D. Este comando genera unos ficheros de muestra en `storage/` para que las URLs funcionen:

```bash
flask --app run seed-storage
```

### 3. Arrancar la API

```bash
flask --app run run --port 8000 --debug
```

La API queda en `http://localhost:8000/api/v1`. Puedes comprobar que responde con `GET /api/v1/services`.

### 4. Arrancar el worker de IA

La generación de diseños y de modelos 3D va en segundo plano. La API deja los trabajos en la tabla `trabajos_ia` y el worker los procesa. Ábrelo en otra terminal:

```bash
flask --app run ai-worker            # se queda escuchando
flask --app run ai-worker --once     # procesa lo pendiente y termina
```

Con `AI_PROVIDER=demo` no se llama a ninguna API externa: los fotogramas se generan a partir de las fotos del cliente, con una etiqueta. Para usar Gemini, pon `AI_PROVIDER=gemini` y tu `GEMINI_API_KEY` de Google AI Studio. La clave vive solo en el servidor; nunca la pongas en la app Android.

## Usuarios de prueba

Todos tienen la contraseña `Evolve2026!`.

| Rol | Email |
|---|---|
| Administrador | daniel@evolve.test |
| Jefe de taller | javier@evolve.test |
| Empleado | andrea@evolve.test |
| Cliente | alex@correo.test |

## Probar con Postman

1. Importa desde la carpeta `postman/` la colección `EVOLVE_API.postman_collection.json` y el entorno `EVOLVE_Local.postman_environment.json`.
2. Selecciona el entorno **EVOLVE · Local** (`baseUrl = http://localhost:8000/api/v1`).
3. Pon `EXPOSE_RESET_TOKEN=true` en tu `.env`.
4. Ejecuta *Run collection*.

Las 133 peticiones pasan de principio a fin si la base de datos está recién cargada. Para repetir la ejecución, vuelve a cargar `sql/evolve_bbdd.sql`.

## Tests

```bash
pytest
```

Los tests no necesitan MySQL: convierten `sql/evolve_bbdd.sql` a SQLite en memoria (con triggers equivalentes) y prueban la API completa. Comprueban:

- auth y permisos por rol;
- el flujo del pedido en cuatro pasos;
- la regla de franjas, que devuelve 409 con `SLOT_TAKEN`;
- presupuestos y pagos;
- el stock y los avisos de stock bajo;
- el calendario;
- los diseños 360 con el worker;
- las réplicas y el GLB generado;
- el proveedor de Gemini, con respuestas simuladas.

## Estructura

```
evolve-api/
├── run.py                   # punto de entrada (flask --app run …)
├── app/
│   ├── __init__.py          # create_app: config, blueprints, errores, CORS
│   ├── config.py            # configuración desde variables de entorno
│   ├── db.py                # acceso a datos con SQL parametrizado y transacciones
│   ├── security.py          # contraseñas, JWT, decoradores de rol y permisos
│   ├── errors.py            # errores de la API y formato JSON común
│   ├── http.py              # utilidades de petición/respuesta y paginación
│   ├── storage.py           # almacenamiento de archivos y URLs firmadas
│   ├── constants.py         # estados, transiciones, ángulos, precios
│   ├── cli.py               # comandos ai-worker y seed-storage
│   ├── schemas/             # validación de entrada (Pydantic)
│   ├── services/            # lógica de negocio reutilizable
│   ├── routes/
│   │   ├── client/          # endpoints de la app Android
│   │   ├── admin/           # endpoints del panel del taller
│   │   └── files.py         # descarga de archivos con URL firmada
│   └── ai/                  # proveedores de imagen, modelo 3D y worker
├── sql/evolve_bbdd.sql      # esquema y datos de prueba
└── tests/                   # pytest
```

Las rutas solo reciben la petición, la validan con su schema y llaman a los servicios. Las reglas de negocio están en `services/`, así que la app y el panel comparten la misma lógica.

## Decisiones principales

- **Autenticación.** El access token es un JWT que dura 15 minutos. El refresh token es opaco y se guarda hasheado en `tokens_usuario`, así que cerrar sesión lo invalida de verdad.
- **Permisos.** Los permisos de cada módulo se leen de `permisos_rol` y se pueden cambiar desde Ajustes. Un empleado solo ve los pedidos que tiene asignados.
- **Franjas horarias.** La regla se aplica en dos capas: la API bloquea la fila del empleado (`SELECT … FOR UPDATE`) y comprueba los solapes, y el trigger `trg_citas_no_solape` de la base de datos actúa de respaldo. Las dos responden con 409 `SLOT_TAKEN`.
- **Stock.** El stock solo cambia a través de `movimientos_stock`, y un trigger mantiene `piezas.stock`. Si una pieza baja del mínimo, la API avisa al personal.
- **Errores.** Todas las respuestas de error tienen el mismo formato:

  ```json
  {"error": {"code": "SLOT_TAKEN", "message": "…", "fields": {…}}}
  ```

## Limitaciones conocidas

- **Gemini.** El proveedor está implementado y tiene tests con respuestas simuladas, pero no se ha probado contra la API real. Antes de la demo, haz una prueba con tu clave.
- **MySQL.** El código para MySQL (PyMySQL) no se ha ejecutado aquí; los tests van sobre SQLite. Si al arrancar ves algún error de SQL, revisa la consulta que indique el log.
- **Modelo 3D.** El proveedor actual es de demostración: genera un GLB y un STL válidos con forma de coche genérico. Para un modelo real basado en las fotos hay que conectar un servicio como TRELLIS o Hunyuan3D en `app/ai/models3d.py`.
- **Notificaciones push.** Las notificaciones se guardan en la base de datos y se consultan por la API. No se envían por FCM.
- **Informes.** Solo se generan en CSV. Si se pide PDF, la API responde `FORMAT_NOT_AVAILABLE`.
