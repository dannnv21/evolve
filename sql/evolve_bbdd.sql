-- =====================================================================
--  EVOLVE · Rebuild your Ride · Base de datos relacional · v3
--  Motor: MySQL 8.0.16+ (Community, GPL) o MariaDB 10.5+  · InnoDB · utf8mb4
--  Versión usada por la API Flask (contraseñas reales de prueba y archivos en almacenamiento local)
--
--  Flujo que cubre:
--    1. El cliente crea un pedido: vehículo, fotos del coche por ángulo,
--       modificaciones elegidas, especificaciones y fotos de las piezas.
--    2. La IA genera una versión de diseño: 8 fotogramas que forman el 360.
--       Cada nueva petición crea otra versión (historial y "volver atrás").
--    3. El taller presupuesta, el cliente acepta o rechaza, se programa,
--       se trabaja (stock, seguimiento), se paga (simulado) y se valora.
--    4. Opcional: réplica en miniatura del coche modificado (modelo 3D
--       generado con IA a partir del 360, en archivo o impresa en el taller).
--
--  Los archivos (fotos, fotogramas, .glb/.stl, informes) NO se guardan aquí:
--  van a un almacenamiento de objetos (MinIO, R2 o disco local) y la tabla
--  `archivos` guarda solo dónde están.
--
--  Módulos:
--    A. Usuarios, roles y acceso      E. Pedidos y presupuestos
--    B. Personal                      F. Diseño IA (360)
--    C. Vehículos y archivos          G. Réplicas en miniatura
--    D. Catálogo, almacén y compras   H. Agenda, pagos, valoraciones,
--                                        notificaciones, ajustes e informes
-- =====================================================================

DROP DATABASE IF EXISTS evolve;
CREATE DATABASE evolve CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE evolve;

-- =====================================================================
--  A. USUARIOS, ROLES Y ACCESO
-- =====================================================================

CREATE TABLE roles (
    id          TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    codigo      VARCHAR(20)  NOT NULL,          -- admin, jefe_taller, empleado, cliente
    nombre      VARCHAR(40)  NOT NULL,
    descripcion VARCHAR(150) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_roles_codigo (codigo)
) ENGINE=InnoDB;

-- Matriz de la pantalla Configuración > Permisos
CREATE TABLE permisos_rol (
    rol_id       TINYINT UNSIGNED NOT NULL,
    modulo       ENUM('pedidos','calendario','almacen','cuentas','equipo',
                      'informes','configuracion') NOT NULL,
    puede_ver    BOOLEAN NOT NULL DEFAULT FALSE,
    puede_editar BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (rol_id, modulo),
    CONSTRAINT fk_permisos_rol FOREIGN KEY (rol_id)
        REFERENCES roles (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE usuarios (
    id                INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    rol_id            TINYINT UNSIGNED NOT NULL,
    nombre_usuario    VARCHAR(30)      NOT NULL,   -- @jruiz (si no lo elige, lo genera el backend)
    nombre            VARCHAR(60)      NOT NULL,
    apellidos         VARCHAR(100)     NOT NULL,
    dni               VARCHAR(12)      NULL,       -- el registro móvil no lo pide; se completa en el perfil
    email             VARCHAR(120)     NOT NULL,
    password_hash     VARCHAR(255)     NOT NULL,   -- bcrypt/argon2, nunca la contraseña
    telefono          VARCHAR(20)      NULL,
    avatar_archivo_id BIGINT UNSIGNED  NULL,       -- FK añadida tras crear `archivos`
    activo            BOOLEAN          NOT NULL DEFAULT TRUE,
    ultimo_acceso     DATETIME         NULL,
    creado_en         DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en    DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP
                                       ON UPDATE CURRENT_TIMESTAMP,
    eliminado_en      DATETIME         NULL,       -- borrado lógico
    PRIMARY KEY (id),
    UNIQUE KEY uq_usuarios_email (email),
    UNIQUE KEY uq_usuarios_nombre_usuario (nombre_usuario),
    UNIQUE KEY uq_usuarios_dni (dni),               -- varios NULL permitidos
    KEY idx_usuarios_rol (rol_id),
    CONSTRAINT fk_usuarios_rol FOREIGN KEY (rol_id) REFERENCES roles (id)
) ENGINE=InnoDB;

-- Tokens de refresco de sesión y de recuperación de contraseña (se guarda el hash)
CREATE TABLE tokens_usuario (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    usuario_id INT UNSIGNED    NOT NULL,
    tipo       ENUM('refresh','reset_password') NOT NULL,
    token_hash CHAR(64)        NOT NULL,          -- SHA-256 del token
    expira_en  DATETIME        NOT NULL,
    usado_en   DATETIME        NULL,              -- reset usado o refresh revocado
    creado_en  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tokens_hash (token_hash),
    KEY idx_tokens_usuario (usuario_id, tipo),
    CONSTRAINT fk_tokens_usuario FOREIGN KEY (usuario_id)
        REFERENCES usuarios (id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Móviles registrados para notificaciones push (Firebase Cloud Messaging, gratuito)
CREATE TABLE dispositivos (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    usuario_id  INT UNSIGNED NOT NULL,
    token_push  VARCHAR(255) NOT NULL,
    plataforma  ENUM('android','ios','web') NOT NULL DEFAULT 'android',
    ultimo_uso  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_dispositivos_token (token_push),
    KEY idx_dispositivos_usuario (usuario_id),
    CONSTRAINT fk_dispositivos_usuario FOREIGN KEY (usuario_id)
        REFERENCES usuarios (id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- =====================================================================
--  B. PERSONAL  (empleado = usuario con rol de taller; relación 1 : 0..1)
-- =====================================================================

CREATE TABLE puestos (
    id     TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    nombre VARCHAR(40) NOT NULL,                  -- Jefe de taller, Especialista de pintura...
    activo BOOLEAN     NOT NULL DEFAULT TRUE,
    PRIMARY KEY (id),
    UNIQUE KEY uq_puestos_nombre (nombre)
) ENGINE=InnoDB;

CREATE TABLE empleados (
    id                  INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    usuario_id          INT UNSIGNED     NOT NULL,
    puesto_id           TINYINT UNSIGNED NOT NULL,
    especialidad        VARCHAR(60)      NULL,     -- "Pintura y vinilos"
    disponibilidad      ENUM('disponible','ausente','vacaciones','baja')
                                         NOT NULL DEFAULT 'disponible',
    ausente_hasta       DATE             NULL,
    capacidad_semanal   TINYINT UNSIGNED NOT NULL DEFAULT 5,  -- pedidos/semana, para el % de carga
    fecha_alta          DATE             NOT NULL,
    fecha_baja          DATE             NULL,     -- "eliminar empleado" = poner fecha de baja
    PRIMARY KEY (id),
    UNIQUE KEY uq_empleados_usuario (usuario_id),
    KEY idx_empleados_puesto (puesto_id),
    CONSTRAINT ck_empleados_capacidad CHECK (capacidad_semanal > 0),
    CONSTRAINT fk_empleados_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
    CONSTRAINT fk_empleados_puesto  FOREIGN KEY (puesto_id)  REFERENCES puestos (id)
) ENGINE=InnoDB;

-- =====================================================================
--  C. ARCHIVOS Y VEHÍCULOS
-- =====================================================================

-- Un registro por cada archivo del almacenamiento de objetos
CREATE TABLE archivos (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    almacenamiento ENUM('local','minio','r2') NOT NULL DEFAULT 'minio',
    bucket         VARCHAR(63)  NOT NULL,
    clave          VARCHAR(255) NOT NULL,          -- ruta del objeto: pedidos/5/v2/trasera.webp
    nombre_original VARCHAR(255) NULL,
    mime           VARCHAR(80)  NOT NULL,          -- image/webp, model/gltf-binary, model/stl, text/csv
    bytes          INT UNSIGNED NOT NULL,
    ancho          SMALLINT UNSIGNED NULL,         -- solo imágenes
    alto           SMALLINT UNSIGNED NULL,
    sha256         CHAR(64)     NULL,              -- detecta subidas duplicadas
    subido_por     INT UNSIGNED NULL,              -- NULL = generado por el sistema (IA)
    creado_en      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_archivos_objeto (bucket, clave),
    KEY idx_archivos_sha (sha256),
    CONSTRAINT fk_archivos_usuario FOREIGN KEY (subido_por)
        REFERENCES usuarios (id) ON DELETE SET NULL
) ENGINE=InnoDB;

ALTER TABLE usuarios
    ADD CONSTRAINT fk_usuarios_avatar FOREIGN KEY (avatar_archivo_id)
        REFERENCES archivos (id) ON DELETE SET NULL;

CREATE TABLE vehiculos (
    id            INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    propietario_id INT UNSIGNED     NOT NULL,      -- dueño actual
    marca         VARCHAR(40)       NOT NULL,
    modelo        VARCHAR(60)       NOT NULL,
    variante      VARCHAR(60)       NULL,          -- "Competition", "Sportback"
    anio          SMALLINT UNSIGNED NULL,
    matricula     VARCHAR(15)       NOT NULL,
    color         VARCHAR(30)       NULL,
    activo        BOOLEAN           NOT NULL DEFAULT TRUE,
    creado_en     DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_vehiculos_matricula (matricula),
    KEY idx_vehiculos_propietario (propietario_id),
    CONSTRAINT ck_vehiculos_anio CHECK (anio IS NULL OR anio BETWEEN 1950 AND 2100),
    CONSTRAINT fk_vehiculos_propietario FOREIGN KEY (propietario_id) REFERENCES usuarios (id)
) ENGINE=InnoDB;

-- Fotos reales del coche, por ángulo. Se reutilizan entre pedidos ("Mi garaje").
-- El ENUM de ángulos está en el orden del giro 360: ORDER BY angulo da el spin.
CREATE TABLE fotos_vehiculo (
    id          INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    vehiculo_id INT UNSIGNED    NOT NULL,
    archivo_id  BIGINT UNSIGNED NOT NULL,
    angulo      ENUM('frontal','frontal_izq','lateral_izq','trasera_izq',
                     'trasera','trasera_der','lateral_der','frontal_der') NOT NULL,
    activa      BOOLEAN         NOT NULL DEFAULT TRUE,   -- FALSE si se sustituye por otra
    creado_en   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_fotos_vehiculo_angulo (vehiculo_id, angulo, activa),
    CONSTRAINT fk_fotos_vehiculo_vehiculo FOREIGN KEY (vehiculo_id)
        REFERENCES vehiculos (id) ON DELETE CASCADE,
    CONSTRAINT fk_fotos_vehiculo_archivo FOREIGN KEY (archivo_id) REFERENCES archivos (id)
) ENGINE=InnoDB;

-- =====================================================================
--  D. CATÁLOGO, ALMACÉN Y COMPRAS
-- =====================================================================

-- Tarjetas del paso 3 del pedido
CREATE TABLE tipos_modificacion (
    id     TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    codigo VARCHAR(30) NOT NULL,                  -- color, vinilo, llantas, suspension...
    nombre VARCHAR(40) NOT NULL,
    icono  VARCHAR(40) NULL,
    activo BOOLEAN     NOT NULL DEFAULT TRUE,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tipos_mod_codigo (codigo)
) ENGINE=InnoDB;

CREATE TABLE servicios (
    id                  SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    tipo_modificacion_id TINYINT UNSIGNED NULL,
    nombre              VARCHAR(80)       NOT NULL,
    descripcion         VARCHAR(255)      NULL,
    precio_base         DECIMAL(10,2)     NOT NULL,   -- mano de obra, sin IVA
    horas_estimadas     DECIMAL(5,1)      NOT NULL DEFAULT 1.0,
    activo              BOOLEAN           NOT NULL DEFAULT TRUE,
    PRIMARY KEY (id),
    UNIQUE KEY uq_servicios_nombre (nombre),
    KEY idx_servicios_tipo (tipo_modificacion_id),
    CONSTRAINT ck_servicios_precio CHECK (precio_base >= 0),
    CONSTRAINT ck_servicios_horas  CHECK (horas_estimadas > 0),
    CONSTRAINT fk_servicios_tipo FOREIGN KEY (tipo_modificacion_id)
        REFERENCES tipos_modificacion (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE categorias_pieza (
    id     TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    nombre VARCHAR(40) NOT NULL,                  -- Carrocería, Vinilos, Llantas...
    PRIMARY KEY (id),
    UNIQUE KEY uq_categorias_pieza_nombre (nombre)
) ENGINE=InnoDB;

CREATE TABLE proveedores (
    id        SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    nombre    VARCHAR(80)  NOT NULL,
    cif       VARCHAR(15)  NULL,
    email     VARCHAR(120) NULL,
    telefono  VARCHAR(20)  NULL,
    web       VARCHAR(255) NULL,
    activo    BOOLEAN      NOT NULL DEFAULT TRUE,
    PRIMARY KEY (id),
    UNIQUE KEY uq_proveedores_nombre (nombre)
) ENGINE=InnoDB;

CREATE TABLE piezas (
    id              INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    referencia      VARCHAR(30)       NOT NULL,   -- KIT-BMW-04
    nombre          VARCHAR(80)       NOT NULL,
    descripcion     VARCHAR(255)      NULL,
    marca           VARCHAR(40)       NULL,
    categoria_id    TINYINT UNSIGNED  NOT NULL,
    proveedor_id    SMALLINT UNSIGNED NULL,       -- proveedor habitual
    precio_venta    DECIMAL(10,2)     NOT NULL,   -- sin IVA
    coste           DECIMAL(10,2)     NULL,
    stock           INT               NOT NULL DEFAULT 0,   -- lo mantiene el trigger de movimientos
    stock_minimo    INT               NOT NULL DEFAULT 0,
    ubicacion       VARCHAR(40)       NULL,       -- "Estantería B · Zona 2"
    imagen_archivo_id BIGINT UNSIGNED NULL,       -- foto de catálogo: sirve de referencia para la IA
    activo          BOOLEAN           NOT NULL DEFAULT TRUE,
    actualizado_en  DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP
                                      ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_piezas_referencia (referencia),
    KEY idx_piezas_categoria (categoria_id),
    KEY idx_piezas_proveedor (proveedor_id),
    CONSTRAINT ck_piezas_precio CHECK (precio_venta >= 0),
    CONSTRAINT ck_piezas_stock  CHECK (stock >= 0 AND stock_minimo >= 0),
    CONSTRAINT fk_piezas_categoria FOREIGN KEY (categoria_id) REFERENCES categorias_pieza (id),
    CONSTRAINT fk_piezas_proveedor FOREIGN KEY (proveedor_id)
        REFERENCES proveedores (id) ON DELETE SET NULL,
    CONSTRAINT fk_piezas_imagen FOREIGN KEY (imagen_archivo_id)
        REFERENCES archivos (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Pedidos de piezas a proveedores (pantalla Almacén > Pedir piezas). Código mostrado: PO-<id>
CREATE TABLE compras (
    id               INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    proveedor_id     SMALLINT UNSIGNED NOT NULL,
    estado           ENUM('borrador','pedido','en_transito','recibido','cancelado')
                                       NOT NULL DEFAULT 'pedido',
    fecha_pedido     DATE              NOT NULL,
    entrega_prevista DATE              NULL,
    recibido_en      DATETIME          NULL,
    notas            VARCHAR(255)      NULL,
    creado_por       INT UNSIGNED      NOT NULL,
    creado_en        DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_compras_estado (estado, entrega_prevista),
    KEY idx_compras_proveedor (proveedor_id),
    CONSTRAINT fk_compras_proveedor FOREIGN KEY (proveedor_id) REFERENCES proveedores (id),
    CONSTRAINT fk_compras_usuario   FOREIGN KEY (creado_por)   REFERENCES usuarios (id)
) ENGINE=InnoDB;

CREATE TABLE compra_lineas (
    compra_id         INT UNSIGNED  NOT NULL,
    pieza_id          INT UNSIGNED  NOT NULL,
    cantidad          INT UNSIGNED  NOT NULL,
    coste_unitario    DECIMAL(10,2) NOT NULL,
    cantidad_recibida INT UNSIGNED  NOT NULL DEFAULT 0,
    PRIMARY KEY (compra_id, pieza_id),
    KEY idx_compra_lineas_pieza (pieza_id),
    CONSTRAINT ck_compra_lineas_cantidad CHECK (cantidad > 0 AND cantidad_recibida <= cantidad),
    CONSTRAINT ck_compra_lineas_coste    CHECK (coste_unitario >= 0),
    CONSTRAINT fk_compra_lineas_compra FOREIGN KEY (compra_id)
        REFERENCES compras (id) ON DELETE CASCADE,
    CONSTRAINT fk_compra_lineas_pieza  FOREIGN KEY (pieza_id) REFERENCES piezas (id)
) ENGINE=InnoDB;

-- =====================================================================
--  E. PEDIDOS Y PRESUPUESTOS   (código mostrado: EV-<id con 4 cifras>)
-- =====================================================================

CREATE TABLE pedidos (
    id                   INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    cliente_id           INT UNSIGNED     NOT NULL,  -- se guarda aunque se deduzca del vehículo:
                                                     -- si el coche cambia de dueño, el historial no se mueve
    vehiculo_id          INT UNSIGNED     NOT NULL,
    responsable_id       INT UNSIGNED     NULL,      -- NULL hasta que el taller lo asigna
    estado               ENUM('borrador','solicitado','presupuestado','aceptado',
                              'rechazado','programado','en_proceso','esperando_piezas',
                              'listo','entregado','cancelado')
                                          NOT NULL DEFAULT 'borrador',
    descripcion_cliente  TEXT             NULL,      -- "Describe las modificaciones"
    especificaciones     JSON             NULL,      -- datos del formulario: códigos de color, medidas...
    version_actual_id    INT UNSIGNED     NULL,      -- versión del 360 que ve el cliente
    version_aprobada_id  INT UNSIGNED     NULL,      -- versión con la que pidió presupuesto
    fecha_preferida      DATE             NULL,      -- paso 4: cuándo quiere traer el coche
    fecha_entrada        DATETIME         NULL,      -- cuándo lo trajo realmente
    entrega_estimada     DATETIME         NULL,
    entrega_real         DATETIME         NULL,
    progreso             TINYINT UNSIGNED NOT NULL DEFAULT 0,
    motivo_rechazo_taller VARCHAR(255)    NULL,      -- la modificación no es posible
    enviado_en           DATETIME         NULL,      -- cuándo dejó de ser borrador
    creado_en            DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en       DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP
                                          ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_pedidos_cliente (cliente_id, estado),
    KEY idx_pedidos_vehiculo (vehiculo_id),
    KEY idx_pedidos_responsable (responsable_id, estado),
    KEY idx_pedidos_estado_entrega (estado, entrega_estimada),
    KEY idx_pedidos_creado (creado_en),
    CONSTRAINT ck_pedidos_progreso CHECK (progreso BETWEEN 0 AND 100),
    CONSTRAINT fk_pedidos_cliente     FOREIGN KEY (cliente_id)     REFERENCES usuarios (id),
    CONSTRAINT fk_pedidos_vehiculo    FOREIGN KEY (vehiculo_id)    REFERENCES vehiculos (id),
    CONSTRAINT fk_pedidos_responsable FOREIGN KEY (responsable_id) REFERENCES empleados (id)
) ENGINE=InnoDB;

-- Tarjetas elegidas en el paso 3 (N:M)
CREATE TABLE pedido_modificaciones (
    pedido_id            INT UNSIGNED     NOT NULL,
    tipo_modificacion_id TINYINT UNSIGNED NOT NULL,
    detalle              VARCHAR(255)     NULL,      -- "Satin Black", "19 pulgadas"
    PRIMARY KEY (pedido_id, tipo_modificacion_id),
    KEY idx_pedido_mod_tipo (tipo_modificacion_id),
    CONSTRAINT fk_pedido_mod_pedido FOREIGN KEY (pedido_id)
        REFERENCES pedidos (id) ON DELETE CASCADE,
    CONSTRAINT fk_pedido_mod_tipo FOREIGN KEY (tipo_modificacion_id)
        REFERENCES tipos_modificacion (id)
) ENGINE=InnoDB;

-- Piezas que el cliente quiere montar: foto subida por él o pieza del catálogo
CREATE TABLE referencias_pieza (
    id                   INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    pedido_id            INT UNSIGNED     NOT NULL,
    tipo_modificacion_id TINYINT UNSIGNED NOT NULL,  -- llantas, kit exterior...
    archivo_id           BIGINT UNSIGNED  NULL,      -- foto que sube el cliente
    pieza_id             INT UNSIGNED     NULL,      -- o pieza de nuestro almacén
    marca                VARCHAR(40)      NULL,
    modelo               VARCHAR(80)      NULL,
    notas                VARCHAR(255)     NULL,
    creado_en            DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_referencias_pedido (pedido_id),
    CONSTRAINT ck_referencias_origen CHECK (archivo_id IS NOT NULL OR pieza_id IS NOT NULL),
    CONSTRAINT fk_referencias_pedido  FOREIGN KEY (pedido_id)
        REFERENCES pedidos (id) ON DELETE CASCADE,
    CONSTRAINT fk_referencias_tipo    FOREIGN KEY (tipo_modificacion_id) REFERENCES tipos_modificacion (id),
    -- Sin ON DELETE en estas dos: MySQL no permite acciones referenciales en columnas usadas en un CHECK
    CONSTRAINT fk_referencias_archivo FOREIGN KEY (archivo_id) REFERENCES archivos (id),
    CONSTRAINT fk_referencias_pieza   FOREIGN KEY (pieza_id)   REFERENCES piezas (id)
) ENGINE=InnoDB;

-- Un pedido puede tener varios presupuestos (si el cliente rechaza, se rehace)
CREATE TABLE presupuestos (
    id             INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    pedido_id      INT UNSIGNED     NOT NULL,
    numero         TINYINT UNSIGNED NOT NULL DEFAULT 1,
    estado         ENUM('borrador','enviado','aceptado','rechazado','caducado')
                                    NOT NULL DEFAULT 'borrador',
    base_imponible DECIMAL(10,2)    NOT NULL DEFAULT 0.00,   -- suma de líneas (la calcula el backend)
    iva_pct        DECIMAL(4,2)     NOT NULL DEFAULT 21.00,
    total          DECIMAL(10,2)    NOT NULL DEFAULT 0.00,
    valido_hasta   DATE             NULL,
    enviado_en     DATETIME         NULL,
    respondido_en  DATETIME         NULL,
    motivo_rechazo VARCHAR(255)     NULL,                    -- el cliente lo rechaza
    creado_por     INT UNSIGNED     NOT NULL,
    creado_en      DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_presupuestos_pedido_numero (pedido_id, numero),
    CONSTRAINT ck_presupuestos_importes CHECK (base_imponible >= 0 AND total >= 0 AND iva_pct >= 0),
    CONSTRAINT fk_presupuestos_pedido  FOREIGN KEY (pedido_id)
        REFERENCES pedidos (id) ON DELETE CASCADE,
    CONSTRAINT fk_presupuestos_usuario FOREIGN KEY (creado_por) REFERENCES usuarios (id)
) ENGINE=InnoDB;

CREATE TABLE presupuesto_lineas (
    id              INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    presupuesto_id  INT UNSIGNED      NOT NULL,
    tipo            ENUM('servicio','pieza','otro') NOT NULL,
    servicio_id     SMALLINT UNSIGNED NULL,
    pieza_id        INT UNSIGNED      NULL,
    descripcion     VARCHAR(120)      NOT NULL,     -- se copia para que no cambie si cambia el catálogo
    cantidad        DECIMAL(8,2)      NOT NULL DEFAULT 1,
    precio_unitario DECIMAL(10,2)     NOT NULL,     -- precio congelado
    importe         DECIMAL(10,2) GENERATED ALWAYS AS (ROUND(cantidad * precio_unitario, 2)) STORED,
    PRIMARY KEY (id),
    KEY idx_presupuesto_lineas_presupuesto (presupuesto_id),
    CONSTRAINT ck_presupuesto_lineas_valores CHECK (cantidad > 0 AND precio_unitario >= 0),
    CONSTRAINT ck_presupuesto_lineas_tipo CHECK (
           (tipo = 'servicio' AND servicio_id IS NOT NULL AND pieza_id IS NULL)
        OR (tipo = 'pieza'    AND pieza_id    IS NOT NULL AND servicio_id IS NULL)
        OR (tipo = 'otro'     AND servicio_id IS NULL     AND pieza_id IS NULL)),
    CONSTRAINT fk_presupuesto_lineas_presupuesto FOREIGN KEY (presupuesto_id)
        REFERENCES presupuestos (id) ON DELETE CASCADE,
    CONSTRAINT fk_presupuesto_lineas_servicio FOREIGN KEY (servicio_id) REFERENCES servicios (id),
    CONSTRAINT fk_presupuesto_lineas_pieza    FOREIGN KEY (pieza_id)    REFERENCES piezas (id)
) ENGINE=InnoDB;

-- Piezas del almacén asignadas al pedido (lo que se monta de verdad)
CREATE TABLE pedido_piezas (
    pedido_id       INT UNSIGNED  NOT NULL,
    pieza_id        INT UNSIGNED  NOT NULL,
    cantidad        INT UNSIGNED  NOT NULL DEFAULT 1,
    precio_unitario DECIMAL(10,2) NOT NULL,
    asignado_en     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (pedido_id, pieza_id),
    KEY idx_pedido_piezas_pieza (pieza_id),
    CONSTRAINT ck_pedido_piezas_valores CHECK (cantidad > 0 AND precio_unitario >= 0),
    CONSTRAINT fk_pedido_piezas_pedido FOREIGN KEY (pedido_id)
        REFERENCES pedidos (id) ON DELETE CASCADE,
    CONSTRAINT fk_pedido_piezas_pieza  FOREIGN KEY (pieza_id) REFERENCES piezas (id)
) ENGINE=InnoDB;

-- Historial de stock. Cada movimiento actualiza piezas.stock (ver trigger)
CREATE TABLE movimientos_stock (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    pieza_id         INT UNSIGNED    NOT NULL,
    tipo             ENUM('entrada_compra','salida_pedido','devolucion','ajuste') NOT NULL,
    cantidad         INT             NOT NULL,     -- positiva entra, negativa sale
    stock_resultante INT             NOT NULL DEFAULT 0,  -- lo rellena el trigger
    pedido_id        INT UNSIGNED    NULL,
    compra_id        INT UNSIGNED    NULL,
    motivo           VARCHAR(255)    NULL,
    usuario_id       INT UNSIGNED    NULL,
    creado_en        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_movimientos_pieza (pieza_id, creado_en),
    KEY idx_movimientos_pedido (pedido_id),
    KEY idx_movimientos_compra (compra_id),
    CONSTRAINT ck_movimientos_cantidad CHECK (cantidad <> 0),
    CONSTRAINT fk_movimientos_pieza   FOREIGN KEY (pieza_id)   REFERENCES piezas (id),
    CONSTRAINT fk_movimientos_pedido  FOREIGN KEY (pedido_id)  REFERENCES pedidos (id) ON DELETE SET NULL,
    CONSTRAINT fk_movimientos_compra  FOREIGN KEY (compra_id)  REFERENCES compras (id) ON DELETE SET NULL,
    CONSTRAINT fk_movimientos_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Bloque "Seguimiento" del detalle del pedido
CREATE TABLE pedido_historial (
    id              BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    pedido_id       INT UNSIGNED     NOT NULL,
    estado_anterior VARCHAR(20)      NULL,
    estado_nuevo    VARCHAR(20)      NOT NULL,
    progreso        TINYINT UNSIGNED NULL,
    nota            VARCHAR(255)     NULL,
    visible_cliente BOOLEAN          NOT NULL DEFAULT TRUE,
    usuario_id      INT UNSIGNED     NULL,
    creado_en       DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_historial_pedido (pedido_id, creado_en),
    CONSTRAINT fk_historial_pedido  FOREIGN KEY (pedido_id)
        REFERENCES pedidos (id) ON DELETE CASCADE,
    CONSTRAINT fk_historial_usuario FOREIGN KEY (usuario_id)
        REFERENCES usuarios (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- =====================================================================
--  F. DISEÑO IA · VISTA 360
--  Cada petición del cliente crea una versión. version_padre_id forma el
--  árbol de historial; "volver atrás" = cambiar pedidos.version_actual_id.
-- =====================================================================

CREATE TABLE versiones_diseno (
    id                INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    pedido_id         INT UNSIGNED     NOT NULL,
    numero            SMALLINT UNSIGNED NOT NULL,   -- 1, 2, 3... dentro del pedido
    version_padre_id  INT UNSIGNED     NULL,
    peticion          TEXT             NOT NULL,     -- lo que escribió el cliente
    plan_director     JSON             NULL,         -- salida del modelo de texto: piezas, ángulo maestro, prompts
    vista_maestra     ENUM('frontal','frontal_izq','lateral_izq','trasera_izq',
                           'trasera','trasera_der','lateral_der','frontal_der') NULL,
    estado            ENUM('pendiente','interpretando','generando','revisando',
                           'listo','error','descartada') NOT NULL DEFAULT 'pendiente',
    proveedor_ia      ENUM('gemini','huggingface','demo') NOT NULL DEFAULT 'gemini',
    modelo_texto      VARCHAR(60)      NULL,         -- se guarda el id exacto del modelo usado
    modelo_imagen     VARCHAR(60)      NULL,
    semilla           INT UNSIGNED     NULL,         -- para "Regenerar concepto"
    error_mensaje     VARCHAR(500)     NULL,
    coste_estimado    DECIMAL(8,4)     NOT NULL DEFAULT 0,  -- en USD; control de los créditos
    creado_por        INT UNSIGNED     NOT NULL,
    creado_en         DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completado_en     DATETIME         NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_versiones_pedido_numero (pedido_id, numero),
    KEY idx_versiones_padre (version_padre_id),
    KEY idx_versiones_usuario_fecha (creado_por, creado_en),   -- límite diario por cliente
    CONSTRAINT fk_versiones_pedido  FOREIGN KEY (pedido_id)
        REFERENCES pedidos (id) ON DELETE CASCADE,
    CONSTRAINT fk_versiones_padre   FOREIGN KEY (version_padre_id)
        REFERENCES versiones_diseno (id) ON DELETE SET NULL,
    CONSTRAINT fk_versiones_usuario FOREIGN KEY (creado_por) REFERENCES usuarios (id)
) ENGINE=InnoDB;

ALTER TABLE pedidos
    ADD CONSTRAINT fk_pedidos_version_actual FOREIGN KEY (version_actual_id)
        REFERENCES versiones_diseno (id) ON DELETE SET NULL,
    ADD CONSTRAINT fk_pedidos_version_aprobada FOREIGN KEY (version_aprobada_id)
        REFERENCES versiones_diseno (id) ON DELETE SET NULL;

-- Qué fotos y referencias se usaron en cada versión (para poder reproducirla
-- aunque el cliente cambie después sus fotos)
CREATE TABLE version_entradas (
    version_id    INT UNSIGNED    NOT NULL,
    archivo_id    BIGINT UNSIGNED NOT NULL,
    rol           ENUM('foto_vehiculo','referencia_pieza','version_previa') NOT NULL,
    angulo        ENUM('frontal','frontal_izq','lateral_izq','trasera_izq',
                       'trasera','trasera_der','lateral_der','frontal_der') NULL,
    referencia_id INT UNSIGNED    NULL,
    PRIMARY KEY (version_id, archivo_id),
    KEY idx_version_entradas_referencia (referencia_id),
    CONSTRAINT fk_version_entradas_version FOREIGN KEY (version_id)
        REFERENCES versiones_diseno (id) ON DELETE CASCADE,
    CONSTRAINT fk_version_entradas_archivo FOREIGN KEY (archivo_id) REFERENCES archivos (id),
    CONSTRAINT fk_version_entradas_referencia FOREIGN KEY (referencia_id)
        REFERENCES referencias_pieza (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Los 8 fotogramas del spin 360 de cada versión (ORDER BY angulo = orden de giro)
CREATE TABLE fotogramas_360 (
    version_id INT UNSIGNED     NOT NULL,
    angulo     ENUM('frontal','frontal_izq','lateral_izq','trasera_izq',
                    'trasera','trasera_der','lateral_der','frontal_der') NOT NULL,
    archivo_id BIGINT UNSIGNED  NOT NULL,
    origen     ENUM('editada','generada') NOT NULL,  -- editada = partía de una foto real de ese ángulo
    intento    TINYINT UNSIGNED NOT NULL DEFAULT 1,  -- sube al regenerar solo este fotograma
    valido     BOOLEAN          NOT NULL DEFAULT TRUE, -- resultado del control de calidad
    creado_en  DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (version_id, angulo),
    KEY idx_fotogramas_archivo (archivo_id),
    CONSTRAINT fk_fotogramas_version FOREIGN KEY (version_id)
        REFERENCES versiones_diseno (id) ON DELETE CASCADE,
    CONSTRAINT fk_fotogramas_archivo FOREIGN KEY (archivo_id) REFERENCES archivos (id)
) ENGINE=InnoDB;

-- =====================================================================
--  G. RÉPLICAS EN MINIATURA (opcional, a partir de una versión de diseño)
--  Modelo 3D con TRELLIS (MIT) o Hunyuan3D en Hugging Face; STL con
--  Blender/trimesh (open source); impresión en la impresora del taller.
-- =====================================================================

CREATE TABLE replicas (
    id                INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    pedido_id         INT UNSIGNED  NOT NULL,
    version_id        INT UNSIGNED  NOT NULL,       -- diseño que se reproduce
    cliente_id        INT UNSIGNED  NOT NULL,
    formato_entrega   ENUM('archivo_digital','impresion_taller') NOT NULL DEFAULT 'impresion_taller',
    escala            ENUM('1:64','1:43','1:32','1:24','1:18') NOT NULL DEFAULT '1:43',
    material          ENUM('PLA','PETG','resina') NULL,   -- solo impresión
    acabado           ENUM('sin_pintar','pintada')  NULL,
    estado            ENUM('solicitada','generando_modelo','modelo_listo','aprobada',
                           'imprimiendo','lista','entregada','cancelada','error')
                                    NOT NULL DEFAULT 'solicitada',
    motor_3d          ENUM('trellis','hunyuan3d','tripo') NOT NULL DEFAULT 'trellis',
    modelo_glb_id     BIGINT UNSIGNED NULL,         -- para el visor <model-viewer>
    modelo_stl_id     BIGINT UNSIGNED NULL,         -- para imprimir
    preview_id        BIGINT UNSIGNED NULL,
    precio            DECIMAL(8,2)  NOT NULL DEFAULT 0.00,  -- sin IVA
    notas             VARCHAR(255)  NULL,
    creado_en         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_replicas_pedido (pedido_id),
    KEY idx_replicas_cliente (cliente_id, estado),
    CONSTRAINT ck_replicas_precio CHECK (precio >= 0),
    CONSTRAINT ck_replicas_impresion CHECK (
        formato_entrega = 'archivo_digital' OR material IS NOT NULL),
    CONSTRAINT fk_replicas_pedido  FOREIGN KEY (pedido_id)  REFERENCES pedidos (id),
    CONSTRAINT fk_replicas_version FOREIGN KEY (version_id) REFERENCES versiones_diseno (id),
    CONSTRAINT fk_replicas_cliente FOREIGN KEY (cliente_id) REFERENCES usuarios (id),
    CONSTRAINT fk_replicas_glb     FOREIGN KEY (modelo_glb_id) REFERENCES archivos (id) ON DELETE SET NULL,
    CONSTRAINT fk_replicas_stl     FOREIGN KEY (modelo_stl_id) REFERENCES archivos (id) ON DELETE SET NULL,
    CONSTRAINT fk_replicas_preview FOREIGN KEY (preview_id)    REFERENCES archivos (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Cola de trabajos de IA en la propia base de datos (sin Redis).
-- El worker coge trabajos con: SELECT ... FOR UPDATE SKIP LOCKED
CREATE TABLE trabajos_ia (
    id               BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    tipo             ENUM('diseno_360','fotograma','control_calidad','modelo_3d') NOT NULL,
    estado           ENUM('en_cola','procesando','completado','error','cancelado')
                                      NOT NULL DEFAULT 'en_cola',
    prioridad        TINYINT UNSIGNED NOT NULL DEFAULT 5,   -- 1 = más urgente
    version_id       INT UNSIGNED     NULL,
    angulo           ENUM('frontal','frontal_izq','lateral_izq','trasera_izq',
                          'trasera','trasera_der','lateral_der','frontal_der') NULL,
    replica_id       INT UNSIGNED     NULL,
    solicitado_por   INT UNSIGNED     NULL,
    proveedor        ENUM('gemini','huggingface','tripo','demo') NOT NULL,
    intentos         TINYINT UNSIGNED NOT NULL DEFAULT 0,
    max_intentos     TINYINT UNSIGNED NOT NULL DEFAULT 3,
    parametros       JSON             NULL,
    resultado        JSON             NULL,
    error_mensaje    VARCHAR(500)     NULL,
    disponible_desde DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- reintentos con espera
    bloqueado_por    VARCHAR(60)      NULL,                 -- id del worker
    creado_en        DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    iniciado_en      DATETIME         NULL,
    finalizado_en    DATETIME         NULL,
    PRIMARY KEY (id),
    KEY idx_trabajos_cola (estado, disponible_desde, prioridad),
    KEY idx_trabajos_version (version_id),
    KEY idx_trabajos_replica (replica_id),
    CONSTRAINT fk_trabajos_version FOREIGN KEY (version_id)
        REFERENCES versiones_diseno (id) ON DELETE CASCADE,
    CONSTRAINT fk_trabajos_replica FOREIGN KEY (replica_id)
        REFERENCES replicas (id) ON DELETE CASCADE,
    CONSTRAINT fk_trabajos_usuario FOREIGN KEY (solicitado_por)
        REFERENCES usuarios (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- =====================================================================
--  H. AGENDA, PAGOS, VALORACIONES, NOTIFICACIONES, AJUSTES E INFORMES
-- =====================================================================

-- Calendario del taller. Un evento puede no tener pedido ni empleado (p. ej. una reunión)
CREATE TABLE citas (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    tipo        ENUM('entrada','trabajo','revision','entrega','otro') NOT NULL,
    pedido_id   INT UNSIGNED NULL,
    empleado_id INT UNSIGNED NULL,
    titulo      VARCHAR(80)  NOT NULL,
    descripcion VARCHAR(255) NULL,
    inicio      DATETIME     NOT NULL,
    fin         DATETIME     NOT NULL,
    estado      ENUM('programada','completada','cancelada') NOT NULL DEFAULT 'programada',
    creado_por  INT UNSIGNED NULL,
    creado_en   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_citas_inicio (inicio),
    KEY idx_citas_empleado (empleado_id, inicio),
    KEY idx_citas_pedido (pedido_id),
    CONSTRAINT ck_citas_rango CHECK (fin > inicio),
    CONSTRAINT fk_citas_pedido   FOREIGN KEY (pedido_id)   REFERENCES pedidos (id) ON DELETE CASCADE,
    CONSTRAINT fk_citas_empleado FOREIGN KEY (empleado_id) REFERENCES empleados (id) ON DELETE SET NULL,
    CONSTRAINT fk_citas_usuario  FOREIGN KEY (creado_por)  REFERENCES usuarios (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Pago simulado: no se cobra nada real
CREATE TABLE pagos (
    id             INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    concepto       ENUM('pedido','replica') NOT NULL DEFAULT 'pedido',
    pedido_id      INT UNSIGNED  NOT NULL,
    presupuesto_id INT UNSIGNED  NULL,
    replica_id     INT UNSIGNED  NULL,
    metodo         ENUM('simulado','tarjeta','efectivo','transferencia','bizum')
                                 NOT NULL DEFAULT 'simulado',
    importe        DECIMAL(10,2) NOT NULL,
    estado         ENUM('pendiente','completado','fallido','reembolsado') NOT NULL DEFAULT 'pendiente',
    referencia     VARCHAR(40)   NULL,          -- id ficticio de la operación
    pagado_en      DATETIME      NULL,
    creado_en      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_pagos_pedido (pedido_id),
    KEY idx_pagos_fecha (pagado_en),
    CONSTRAINT ck_pagos_importe CHECK (importe > 0),
    CONSTRAINT ck_pagos_concepto CHECK (
           (concepto = 'pedido'  AND replica_id IS NULL)
        OR (concepto = 'replica' AND replica_id IS NOT NULL)),
    CONSTRAINT fk_pagos_pedido      FOREIGN KEY (pedido_id)      REFERENCES pedidos (id),
    CONSTRAINT fk_pagos_presupuesto FOREIGN KEY (presupuesto_id) REFERENCES presupuestos (id) ON DELETE SET NULL,
    CONSTRAINT fk_pagos_replica     FOREIGN KEY (replica_id)     REFERENCES replicas (id)
) ENGINE=InnoDB;

CREATE TABLE valoraciones (
    id         INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    pedido_id  INT UNSIGNED     NOT NULL,
    cliente_id INT UNSIGNED     NOT NULL,
    puntuacion TINYINT UNSIGNED NOT NULL,
    comentario VARCHAR(1000)    NULL,
    creado_en  DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_valoraciones_pedido (pedido_id),     -- una por pedido
    KEY idx_valoraciones_fecha (creado_en),
    CONSTRAINT ck_valoraciones_puntuacion CHECK (puntuacion BETWEEN 1 AND 5),
    CONSTRAINT fk_valoraciones_pedido  FOREIGN KEY (pedido_id)  REFERENCES pedidos (id) ON DELETE CASCADE,
    CONSTRAINT fk_valoraciones_cliente FOREIGN KEY (cliente_id) REFERENCES usuarios (id)
) ENGINE=InnoDB;

CREATE TABLE notificaciones (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    usuario_id INT UNSIGNED    NOT NULL,
    tipo       ENUM('estado_pedido','presupuesto','diseno_listo','replica',
                    'solicitud_nueva','stock_bajo','entrega_hoy','sistema') NOT NULL,
    titulo     VARCHAR(120)    NOT NULL,
    cuerpo     VARCHAR(255)    NULL,
    pedido_id  INT UNSIGNED    NULL,
    pieza_id   INT UNSIGNED    NULL,
    replica_id INT UNSIGNED    NULL,
    leida_en   DATETIME        NULL,
    creado_en  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_notificaciones_usuario (usuario_id, leida_en, creado_en),
    CONSTRAINT fk_notificaciones_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE,
    CONSTRAINT fk_notificaciones_pedido  FOREIGN KEY (pedido_id)  REFERENCES pedidos (id)  ON DELETE CASCADE,
    CONSTRAINT fk_notificaciones_pieza   FOREIGN KEY (pieza_id)   REFERENCES piezas (id)   ON DELETE CASCADE,
    CONSTRAINT fk_notificaciones_replica FOREIGN KEY (replica_id) REFERENCES replicas (id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Interruptores de Perfil > Notificaciones (una fila por tipo que el usuario cambia)
CREATE TABLE preferencias_notificacion (
    usuario_id INT UNSIGNED NOT NULL,
    tipo       ENUM('estado_pedido','presupuesto','diseno_listo','replica',
                    'solicitud_nueva','stock_bajo','entrega_hoy','sistema') NOT NULL,
    push       BOOLEAN NOT NULL DEFAULT TRUE,
    email      BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (usuario_id, tipo),
    CONSTRAINT fk_preferencias_usuario FOREIGN KEY (usuario_id)
        REFERENCES usuarios (id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Configuración del taller en formato clave/valor (pestañas General, Datos del taller,
-- Notificaciones). Añadir un ajuste nuevo no exige cambiar la estructura.
CREATE TABLE ajustes_taller (
    clave          VARCHAR(50)  NOT NULL,      -- general, negocio, avisos_automaticos, ia
    valor          JSON         NOT NULL,
    actualizado_por INT UNSIGNED NULL,
    actualizado_en DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (clave),
    CONSTRAINT fk_ajustes_usuario FOREIGN KEY (actualizado_por)
        REFERENCES usuarios (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE informes (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    tipo            ENUM('rendimiento','facturacion','almacen','valoraciones') NOT NULL,
    formato         ENUM('csv','pdf') NOT NULL DEFAULT 'csv',
    nombre          VARCHAR(120) NOT NULL,
    periodo_inicio  DATE         NOT NULL,
    periodo_fin     DATE         NOT NULL,
    archivo_id      BIGINT UNSIGNED NULL,
    creado_por      INT UNSIGNED NOT NULL,
    creado_en       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT ck_informes_periodo CHECK (periodo_fin >= periodo_inicio),
    CONSTRAINT fk_informes_archivo FOREIGN KEY (archivo_id) REFERENCES archivos (id) ON DELETE SET NULL,
    CONSTRAINT fk_informes_usuario FOREIGN KEY (creado_por) REFERENCES usuarios (id)
) ENGINE=InnoDB;

-- =====================================================================
--  VISTAS
-- =====================================================================

-- Tabla de pedidos del panel y "Mis reservas"
CREATE VIEW v_pedidos AS
SELECT p.id,
       CONCAT('EV-', LPAD(p.id, 4, '0'))          AS codigo,
       p.estado, p.progreso, p.entrega_estimada, p.fecha_preferida,
       c.id                                       AS cliente_id,
       CONCAT(c.nombre, ' ', c.apellidos)         AS cliente,
       v.id                                       AS vehiculo_id,
       CONCAT_WS(' ', v.marca, v.modelo, v.variante) AS vehiculo,
       v.matricula,
       p.responsable_id,
       CONCAT(ru.nombre, ' ', ru.apellidos)       AS responsable,
       (SELECT pr.total FROM presupuestos pr
         WHERE pr.pedido_id = p.id AND pr.estado <> 'borrador'
         ORDER BY pr.numero DESC LIMIT 1)         AS presupuesto_total,
       (SELECT GROUP_CONCAT(t.nombre ORDER BY t.id SEPARATOR ' + ')
          FROM pedido_modificaciones pm
          JOIN tipos_modificacion t ON t.id = pm.tipo_modificacion_id
         WHERE pm.pedido_id = p.id)               AS servicio,
       p.creado_en
FROM pedidos p
JOIN usuarios  c  ON c.id = p.cliente_id
JOIN vehiculos v  ON v.id = p.vehiculo_id
LEFT JOIN empleados e  ON e.id = p.responsable_id
LEFT JOIN usuarios  ru ON ru.id = e.usuario_id;

-- Inventario con el estado del Figma (crítico / bajo / correcto)
CREATE VIEW v_stock AS
SELECT pz.id, pz.referencia, pz.nombre, cp.nombre AS categoria,
       pz.stock, pz.stock_minimo, pz.ubicacion,
       CASE WHEN pz.stock <= pz.stock_minimo     THEN 'critico'
            WHEN pz.stock <= pz.stock_minimo * 2 THEN 'bajo'
            ELSE 'correcto' END AS estado_stock
FROM piezas pz
JOIN categorias_pieza cp ON cp.id = pz.categoria_id
WHERE pz.activo = TRUE;

-- "Carga del equipo": pedidos activos por empleado frente a su capacidad
CREATE VIEW v_carga_empleados AS
SELECT e.id AS empleado_id,
       CONCAT(u.nombre, ' ', u.apellidos) AS empleado,
       pu.nombre AS puesto, e.especialidad, e.disponibilidad,
       COUNT(p.id) AS pedidos_activos,
       ROUND(100 * COUNT(p.id) / e.capacidad_semanal) AS carga_pct
FROM empleados e
JOIN usuarios u ON u.id = e.usuario_id
JOIN puestos pu ON pu.id = e.puesto_id
LEFT JOIN pedidos p ON p.responsable_id = e.id
     AND p.estado IN ('aceptado','programado','en_proceso','esperando_piezas')
WHERE e.fecha_baja IS NULL
GROUP BY e.id, u.nombre, u.apellidos, pu.nombre, e.especialidad,
         e.disponibilidad, e.capacidad_semanal;

-- El 360 actual de cada pedido, fotograma a fotograma y en orden de giro
CREATE VIEW v_spin_actual AS
SELECT p.id AS pedido_id, vd.id AS version_id, vd.numero AS version_numero,
       f.angulo, a.bucket, a.clave
FROM pedidos p
JOIN versiones_diseno vd ON vd.id = p.version_actual_id
JOIN fotogramas_360 f    ON f.version_id = vd.id
JOIN archivos a          ON a.id = f.archivo_id;

-- =====================================================================
--  TRIGGERS
-- =====================================================================
DELIMITER $$

-- Ningún empleado puede tener dos citas solapadas
CREATE TRIGGER trg_citas_no_solape_ins
BEFORE INSERT ON citas
FOR EACH ROW
BEGIN
    IF NEW.empleado_id IS NOT NULL AND NEW.estado <> 'cancelada' AND EXISTS (
        SELECT 1 FROM citas c
        WHERE c.empleado_id = NEW.empleado_id
          AND c.estado <> 'cancelada'
          AND NEW.inicio < c.fin
          AND NEW.fin    > c.inicio
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Franja horaria ocupada para este empleado';
    END IF;
END$$

CREATE TRIGGER trg_citas_no_solape_upd
BEFORE UPDATE ON citas
FOR EACH ROW
BEGIN
    IF NEW.empleado_id IS NOT NULL AND NEW.estado <> 'cancelada' AND EXISTS (
        SELECT 1 FROM citas c
        WHERE c.empleado_id = NEW.empleado_id
          AND c.id <> NEW.id
          AND c.estado <> 'cancelada'
          AND NEW.inicio < c.fin
          AND NEW.fin    > c.inicio
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Franja horaria ocupada para este empleado';
    END IF;
END$$

-- Cada movimiento de almacén actualiza el stock y no deja que baje de 0
CREATE TRIGGER trg_movimientos_stock_bi
BEFORE INSERT ON movimientos_stock
FOR EACH ROW
BEGIN
    DECLARE v_stock INT DEFAULT NULL;

    SELECT stock INTO v_stock FROM piezas WHERE id = NEW.pieza_id FOR UPDATE;

    IF v_stock IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'La pieza no existe';
    END IF;
    IF v_stock + NEW.cantidad < 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Stock insuficiente para esta pieza';
    END IF;

    SET NEW.stock_resultante = v_stock + NEW.cantidad;
    UPDATE piezas SET stock = NEW.stock_resultante WHERE id = NEW.pieza_id;
END$$

DELIMITER ;

-- =====================================================================
--  DATOS DE PRUEBA (ficticios). Contraseña de todos los usuarios: Evolve2026!
-- =====================================================================

INSERT INTO roles (codigo, nombre, descripcion) VALUES
 ('admin',       'Administrador',      'Acceso completo al panel'),
 ('jefe_taller', 'Jefe de taller',     'Gestiona pedidos, agenda, equipo y almacén'),
 ('empleado',    'Empleado / Mecánico','Trabaja en los pedidos asignados'),
 ('cliente',     'Cliente',            'App móvil');

INSERT INTO permisos_rol (rol_id, modulo, puede_ver, puede_editar) VALUES
 (1,'pedidos',TRUE,TRUE),(1,'calendario',TRUE,TRUE),(1,'almacen',TRUE,TRUE),(1,'cuentas',TRUE,TRUE),
 (1,'equipo',TRUE,TRUE),(1,'informes',TRUE,TRUE),(1,'configuracion',TRUE,TRUE),
 (2,'pedidos',TRUE,TRUE),(2,'calendario',TRUE,TRUE),(2,'almacen',TRUE,TRUE),(2,'cuentas',FALSE,FALSE),
 (2,'equipo',TRUE,TRUE),(2,'informes',TRUE,FALSE),(2,'configuracion',FALSE,FALSE),
 (3,'pedidos',TRUE,TRUE),(3,'calendario',TRUE,FALSE),(3,'almacen',TRUE,FALSE),(3,'cuentas',FALSE,FALSE),
 (3,'equipo',FALSE,FALSE),(3,'informes',FALSE,FALSE),(3,'configuracion',FALSE,FALSE);

INSERT INTO puestos (nombre) VALUES
 ('Gerente'), ('Jefe de taller'), ('Especialista de pintura'), ('Detailer'), ('Mecánico');

INSERT INTO usuarios (rol_id, nombre_usuario, nombre, apellidos, dni, email, password_hash, telefono) VALUES
 (1, 'dgarcia',  'Daniel', 'García',  '11111111H', 'daniel@evolve.test', 'scrypt:32768:8:1$4zdp0ToziI4AUTqE$955302d929b8951525c2fd51ca15165094a853e93a253b75652237bc3e1b57e556e55d1fd754f10c7959aa93cc881d10e51ce404e058fd1ba12eacf4c81df792', '600000001'),
 (2, 'jruiz',    'Javier', 'Ruiz',    '22222222J', 'javier@evolve.test', 'scrypt:32768:8:1$4zdp0ToziI4AUTqE$955302d929b8951525c2fd51ca15165094a853e93a253b75652237bc3e1b57e556e55d1fd754f10c7959aa93cc881d10e51ce404e058fd1ba12eacf4c81df792', '600000002'),
 (3, 'amartin',  'Andrea', 'Martín',  '33333333P', 'andrea@evolve.test', 'scrypt:32768:8:1$4zdp0ToziI4AUTqE$955302d929b8951525c2fd51ca15165094a853e93a253b75652237bc3e1b57e556e55d1fd754f10c7959aa93cc881d10e51ce404e058fd1ba12eacf4c81df792', '600000003'),
 (3, 'dperez',   'Diego',  'Pérez',   '44444444A', 'diego@evolve.test',  'scrypt:32768:8:1$4zdp0ToziI4AUTqE$955302d929b8951525c2fd51ca15165094a853e93a253b75652237bc3e1b57e556e55d1fd754f10c7959aa93cc881d10e51ce404e058fd1ba12eacf4c81df792',  '600000004'),
 (3, 'lcano',    'Laura',  'Cano',    '55555555K', 'laura@evolve.test',  'scrypt:32768:8:1$4zdp0ToziI4AUTqE$955302d929b8951525c2fd51ca15165094a853e93a253b75652237bc3e1b57e556e55d1fd754f10c7959aa93cc881d10e51ce404e058fd1ba12eacf4c81df792',  '600000005'),
 (4, 'mvidal',   'Marcos', 'Vidal',   NULL,        'marcos@correo.test', 'scrypt:32768:8:1$4zdp0ToziI4AUTqE$955302d929b8951525c2fd51ca15165094a853e93a253b75652237bc3e1b57e556e55d1fd754f10c7959aa93cc881d10e51ce404e058fd1ba12eacf4c81df792', '611111111'),
 (4, 'lortega',  'Lucía',  'Ortega',  NULL,        'lucia@correo.test',  'scrypt:32768:8:1$4zdp0ToziI4AUTqE$955302d929b8951525c2fd51ca15165094a853e93a253b75652237bc3e1b57e556e55d1fd754f10c7959aa93cc881d10e51ce404e058fd1ba12eacf4c81df792',  '622222222'),
 (4, 'smena',    'Sergio', 'Mena',    NULL,        'sergio@correo.test', 'scrypt:32768:8:1$4zdp0ToziI4AUTqE$955302d929b8951525c2fd51ca15165094a853e93a253b75652237bc3e1b57e556e55d1fd754f10c7959aa93cc881d10e51ce404e058fd1ba12eacf4c81df792', '633333333'),
 (4, 'ncosta',   'Nora',   'Costa',   NULL,        'nora@correo.test',   'scrypt:32768:8:1$4zdp0ToziI4AUTqE$955302d929b8951525c2fd51ca15165094a853e93a253b75652237bc3e1b57e556e55d1fd754f10c7959aa93cc881d10e51ce404e058fd1ba12eacf4c81df792',   '644444444'),
 (4, 'amartinez','Alex',   'Martínez','66666666Q', 'alex@correo.test',   'scrypt:32768:8:1$4zdp0ToziI4AUTqE$955302d929b8951525c2fd51ca15165094a853e93a253b75652237bc3e1b57e556e55d1fd754f10c7959aa93cc881d10e51ce404e058fd1ba12eacf4c81df792',   '655555555');

INSERT INTO empleados (usuario_id, puesto_id, especialidad, disponibilidad, ausente_hasta, capacidad_semanal, fecha_alta) VALUES
 (1, 1, 'Dirección',               'disponible', NULL,         2, '2024-01-15'),
 (2, 2, 'Mecánica y suspensión',   'disponible', NULL,         5, '2024-03-01'),
 (3, 3, 'Pintura y vinilos',       'disponible', NULL,         5, '2024-06-10'),
 (4, 4, 'Detailing y acabados',    'disponible', NULL,         5, '2025-02-03'),
 (5, 5, 'Carrocería y montaje',    'ausente',    '2026-11-27', 5, '2025-09-01');

-- Archivos: fotos del cliente, referencias, fotogramas del 360 y modelos 3D
INSERT INTO archivos (almacenamiento, bucket, clave, nombre_original, mime, bytes, ancho, alto, subido_por) VALUES
 ('local','evolve','vehiculos/5/frontal_izq.jpg','camry_frontal.jpg','image/jpeg',1295534,1024,1024,10),
 ('local','evolve','vehiculos/5/trasera_der.jpg','camry_trasera.jpg','image/jpeg',2908382,1024,1024,10),
 ('local','evolve','pedidos/5/referencias/aleron.jpg','aleron.jpg','image/jpeg',2286667,1024,1024,10),
 ('local','evolve','pedidos/5/referencias/llanta.webp','llantas_top.webp','image/webp',82736,1024,1024,10);

INSERT INTO archivos (almacenamiento, bucket, clave, mime, bytes, ancho, alto)
SELECT 'local', 'evolve', CONCAT('pedidos/5/v', v.n, '/', a.angulo, '.webp'), 'image/webp', 180000, 1024, 1024
FROM (SELECT 1 AS n UNION ALL SELECT 2) v
CROSS JOIN (SELECT 'frontal' AS angulo UNION ALL SELECT 'frontal_izq' UNION ALL SELECT 'lateral_izq'
            UNION ALL SELECT 'trasera_izq' UNION ALL SELECT 'trasera' UNION ALL SELECT 'trasera_der'
            UNION ALL SELECT 'lateral_der' UNION ALL SELECT 'frontal_der') a;

INSERT INTO archivos (almacenamiento, bucket, clave, mime, bytes) VALUES
 ('local','evolve','replicas/1/modelo.glb','model/gltf-binary',6400000),
 ('local','evolve','replicas/1/modelo.stl','model/stl',          9800000),
 ('local','evolve','replicas/1/preview.webp','image/webp',        150000);

INSERT INTO vehiculos (propietario_id, marca, modelo, variante, anio, matricula, color) VALUES
 (6,  'BMW',     'M4',       'Competition', 2022, '4521LMB', 'Gris Brooklyn'),
 (7,  'Audi',    'RS3',      'Sportback',   2023, '7788MNC', 'Blanco Glaciar'),
 (8,  'Cupra',   'Formentor','VZ',          2021, '1290LKD', 'Azul Petrol'),
 (9,  'Porsche', '718',      'Cayman',      2020, '3344KZX', 'Negro'),
 (10, 'Toyota',  'Camry',    NULL,          2014, '8812HJT', 'Plata');

INSERT INTO fotos_vehiculo (vehiculo_id, archivo_id, angulo) VALUES
 (5, 1, 'frontal_izq'),
 (5, 2, 'trasera_der');

INSERT INTO tipos_modificacion (codigo, nombre, icono) VALUES
 ('color',        'Cambio de color', 'palette'),
 ('vinilo',       'Vinilo / wrap',   'layers'),
 ('llantas',      'Llantas',         'disc'),
 ('suspension',   'Suspensión',      'arrows-vertical'),
 ('kit_exterior', 'Kit exterior',    'car'),
 ('iluminacion',  'Iluminación',     'lightbulb');

INSERT INTO servicios (tipo_modificacion_id, nombre, descripcion, precio_base, horas_estimadas) VALUES
 (2, 'Vinilo integral',               'Wrap completo de carrocería',                1450.00, 24.0),
 (1, 'Pintura completa',              'Cambio de color con pintura',                3200.00, 60.0),
 (3, 'Montaje y equilibrado de llantas','Juego de 4 llantas',                        120.00,  2.0),
 (4, 'Instalación suspensión roscada','Montaje y reglaje de altura',                 380.00,  6.0),
 (5, 'Montaje de kit aerodinámico',   'Paragolpes, faldones y difusor',              450.00,  8.0),
 (5, 'Montaje de alerón',             'Alerón trasero sobre el maletero',            180.00,  3.0),
 (1, 'Pintura de pinzas de freno',    'Pinzas a color con barniz cerámico',          220.00,  4.0),
 (NULL,'Detailing y recubrimiento cerámico','Pulido y protección cerámica',          350.00,  6.0),
 (6, 'Instalación de iluminación LED','Faros, pilotos o ambiente',                  150.00,  3.0);

INSERT INTO categorias_pieza (nombre) VALUES
 ('Carrocería'), ('Vinilos'), ('Llantas'), ('Pintura'), ('Suspensión'), ('Detailing');

INSERT INTO proveedores (nombre, cif, email, web) VALUES
 ('Maxton Design',   NULL, 'pedidos@maxton.test',  'https://maxtondesign.com'),
 ('Vossen Europe',   NULL, 'orders@vossen.test',   'https://vossenwheels.com'),
 ('3M Automotive',   NULL, 'pedidos@3m.test',      'https://www.3m.com'),
 ('KW Suspensions',  NULL, 'orders@kw.test',       'https://www.kwsuspensions.com');

-- Stock inicial ANTES de los movimientos de prueba (el trigger los descuenta después)
INSERT INTO piezas (referencia, nombre, marca, categoria_id, proveedor_id, precio_venta, coste, stock, stock_minimo, ubicacion) VALUES
 ('KIT-BMW-04',  'Kit aerodinámico BMW M4',          'Maxton', 1, 1, 1290.00, 820.00,  2, 2, 'Estantería B · Zona 2'),
 ('VIN-SAT-BLK', 'Vinilo Satin Black · 1,52 m',      '3M',     2, 3,   95.00,  52.00, 14, 3, 'Estantería A · Zona 1'),
 ('LLN-19-RS3',  'Llanta 19" RS3 · Graphite',        'Vossen', 3, 2,  420.00, 290.00,  4, 3, 'Almacén llantas'),
 ('PIN-BREM-RED','Pintura pinzas Brembo Red',        NULL,     4, 3,   45.00,  18.00, 14, 4, 'Cabina pintura'),
 ('SUS-KW-V3',   'Suspensión KW Variant 3',          'KW',     5, 4, 1650.00,1180.00,  1, 2, 'Estantería C · Zona 1'),
 ('CER-COAT-01', 'Recubrimiento cerámico 500 ml',    NULL,     6, 3,   60.00,  28.00,  7, 3, 'Detailing'),
 ('ALE-GT-CF',   'Alerón GT fibra de carbono',       'Maxton', 1, 1,  390.00, 240.00,  3, 1, 'Estantería B · Zona 3'),
 ('LLN-18-MB',   'Llanta 18" multirradio negro mate',NULL,     3, 2,  210.00, 130.00,  8, 4, 'Almacén llantas');

INSERT INTO compras (proveedor_id, estado, fecha_pedido, entrega_prevista, creado_por) VALUES
 (1, 'en_transito', '2026-11-18', '2026-11-26', 2),   -- PO-0001: kit BMW
 (4, 'pedido',      '2026-11-20', '2026-11-28', 2),   -- PO-0002: suspensión KW
 (3, 'en_transito', '2026-11-19', '2026-11-25', 2);   -- PO-0003: vinilo

INSERT INTO compra_lineas (compra_id, pieza_id, cantidad, coste_unitario) VALUES
 (1, 1, 2,  820.00),
 (2, 5, 2, 1180.00),
 (3, 2, 10,  52.00);

-- Pedidos (cliente, vehículo, responsable = id de empleados)
INSERT INTO pedidos (cliente_id, vehiculo_id, responsable_id, estado, descripcion_cliente, especificaciones,
                     fecha_preferida, fecha_entrada, entrega_estimada, entrega_real, progreso, enviado_en, creado_en) VALUES
 (6, 1, 2, 'en_proceso',       'Wrap completo en negro satinado, también el techo.',
     JSON_OBJECT('vinilo', 'Satin Black', 'incluye_techo', TRUE),
     '2026-11-17', '2026-11-17 09:30:00', '2026-11-26 18:00:00', NULL, 62, '2026-11-10 20:15:00', '2026-11-10 20:02:00'),
 (7, 2, 3, 'esperando_piezas', 'Llantas Vossen 19 graphite y bajar la suspensión 30 mm.',
     JSON_OBJECT('llanta_pulgadas', 19, 'bajada_mm', 30),
     '2026-11-19', '2026-11-19 10:00:00', '2026-11-28 18:00:00', NULL, 20, '2026-11-12 11:40:00', '2026-11-12 11:31:00'),
 (8, 3, NULL, 'solicitado',    'Kit aerodinámico completo, estilo agresivo.',
     NULL,
     '2026-12-01', NULL, NULL, NULL, 0, '2026-11-22 18:05:00', '2026-11-22 17:50:00'),
 (9, 4, 4, 'entregado',        'Pinzas en rojo Brembo y tratamiento cerámico.',
     JSON_OBJECT('color_pinzas', 'Brembo Red'),
     '2026-11-05', '2026-11-05 09:00:00', '2026-11-07 18:00:00', '2026-11-07 17:30:00', 100, '2026-11-01 10:00:00', '2026-11-01 09:48:00'),
 (10, 5, NULL, 'presupuestado','Quiero montar este alerón en concreto y estas llantas negras.',
     JSON_OBJECT('aleron', 'GT carbono', 'llantas', 'multirradio negro mate'),
     '2026-12-03', NULL, NULL, NULL, 0, '2026-11-21 19:20:00', '2026-11-21 18:30:00'),
 (10, 5, 4, 'entregado',       'Pinzas de freno en rojo.',
     JSON_OBJECT('color_pinzas', 'Brembo Red'),
     '2026-10-14', '2026-10-14 09:00:00', '2026-10-15 18:00:00', '2026-10-15 17:00:00', 100, '2026-10-10 12:00:00', '2026-10-10 11:52:00'),
 (10, 5, NULL, 'presupuestado','Tiras LED en los bajos y pilotos ahumados.',
     NULL,
     '2026-12-10', NULL, NULL, NULL, 0, '2026-11-22 20:10:00', '2026-11-22 20:01:00');

INSERT INTO pedido_modificaciones (pedido_id, tipo_modificacion_id, detalle) VALUES
 (1, 2, 'Satin Black'),
 (2, 3, '19" Graphite'), (2, 4, 'Roscada, -30 mm'),
 (3, 5, 'Kit completo'),
 (4, 1, 'Pinzas Brembo Red'),
 (5, 5, 'Alerón GT carbono'), (5, 3, '18" negro mate'),
 (6, 1, 'Pinzas Brembo Red'),
 (7, 6, 'LED bajos + pilotos');

INSERT INTO referencias_pieza (pedido_id, tipo_modificacion_id, archivo_id, pieza_id, marca, modelo) VALUES
 (5, 5, 3, 7,    NULL, 'Alerón GT carbono'),    -- foto del cliente que coincide con una pieza nuestra
 (5, 3, 4, NULL, NULL, 'Multirradio 15 radios'),
 (3, 5, NULL, 1, 'Maxton', 'Kit BMW');          -- ejemplo de referencia solo de catálogo

-- Presupuestos (base = suma de líneas; total = base + 21 % IVA)
INSERT INTO presupuestos (pedido_id, numero, estado, base_imponible, iva_pct, total, valido_hasta,
                          enviado_en, respondido_en, creado_por) VALUES
 (1, 1, 'aceptado', 2020.00, 21.00, 2444.20, '2026-11-25', '2026-11-11 12:00:00', '2026-11-11 19:30:00', 2),
 (2, 1, 'aceptado', 3830.00, 21.00, 4634.30, '2026-11-27', '2026-11-13 10:00:00', '2026-11-13 21:10:00', 2),
 (4, 1, 'aceptado',  675.00, 21.00,  816.75, '2026-11-15', '2026-11-01 16:00:00', '2026-11-01 18:45:00', 2),
 (5, 1, 'enviado',  1530.00, 21.00, 1851.30, '2026-12-07', '2026-11-22 12:30:00', NULL,                  2),
 (6, 1, 'aceptado',  265.00, 21.00,  320.65, '2026-10-24', '2026-10-10 16:00:00', '2026-10-10 18:00:00', 2),
 (7, 1, 'enviado',   150.00, 21.00,  181.50, '2026-12-07', '2026-11-23 10:00:00', NULL,                  2);

INSERT INTO presupuesto_lineas (presupuesto_id, tipo, servicio_id, pieza_id, descripcion, cantidad, precio_unitario) VALUES
 (1, 'servicio', 1,    NULL, 'Vinilo integral',                    1, 1450.00),
 (1, 'pieza',    NULL, 2,    'Vinilo Satin Black · 1,52 m',        6,   95.00),
 (2, 'servicio', 3,    NULL, 'Montaje y equilibrado de llantas',   1,  120.00),
 (2, 'pieza',    NULL, 3,    'Llanta 19" RS3 · Graphite',          4,  420.00),
 (2, 'servicio', 4,    NULL, 'Instalación suspensión roscada',     1,  380.00),
 (2, 'pieza',    NULL, 5,    'Suspensión KW Variant 3',            1, 1650.00),
 (3, 'servicio', 7,    NULL, 'Pintura de pinzas de freno',         1,  220.00),
 (3, 'pieza',    NULL, 4,    'Pintura pinzas Brembo Red',          1,   45.00),
 (3, 'servicio', 8,    NULL, 'Detailing y recubrimiento cerámico', 1,  350.00),
 (3, 'pieza',    NULL, 6,    'Recubrimiento cerámico 500 ml',      1,   60.00),
 (4, 'servicio', 6,    NULL, 'Montaje de alerón',                  1,  180.00),
 (4, 'pieza',    NULL, 7,    'Alerón GT fibra de carbono',         1,  390.00),
 (4, 'servicio', 3,    NULL, 'Montaje y equilibrado de llantas',   1,  120.00),
 (4, 'pieza',    NULL, 8,    'Llanta 18" multirradio negro mate',  4,  210.00),
 (5, 'servicio', 7,    NULL, 'Pintura de pinzas de freno',         1,  220.00),
 (5, 'pieza',    NULL, 4,    'Pintura pinzas Brembo Red',          1,   45.00),
 (6, 'servicio', 9,    NULL, 'Instalación de iluminación LED',     1,  150.00);

-- Piezas montadas y su salida del almacén (el trigger deja el stock como en el Figma)
INSERT INTO pedido_piezas (pedido_id, pieza_id, cantidad, precio_unitario) VALUES
 (1, 2, 6, 95.00),
 (4, 4, 1, 45.00),
 (4, 6, 1, 60.00),
 (6, 4, 1, 45.00);

INSERT INTO movimientos_stock (pieza_id, tipo, cantidad, pedido_id, usuario_id, motivo) VALUES
 (2, 'salida_pedido', -6, 1, 3, 'Wrap BMW M4'),
 (4, 'salida_pedido', -1, 4, 4, 'Pinzas Porsche 718'),
 (6, 'salida_pedido', -1, 4, 4, 'Cerámico Porsche 718'),
 (4, 'salida_pedido', -1, 6, 4, 'Pinzas Toyota Camry');
-- Resultado: VIN-SAT-BLK 8 uds., PIN-BREM-RED 12, CER-COAT-01 6

INSERT INTO pedido_historial (pedido_id, estado_anterior, estado_nuevo, progreso, nota, usuario_id, creado_en) VALUES
 (1, 'borrador',      'solicitado',    0, 'Solicitud recibida',                  6, '2026-11-10 20:15:00'),
 (1, 'solicitado',    'presupuestado', 0, 'Presupuesto enviado',                 2, '2026-11-11 12:00:00'),
 (1, 'presupuestado', 'aceptado',      0, 'Presupuesto aceptado por el cliente', 6, '2026-11-11 19:30:00'),
 (1, 'aceptado',      'programado',    0, 'Entrada el 17/11 a las 09:30',        2, '2026-11-12 09:00:00'),
 (1, 'programado',    'en_proceso',   10, 'Vehículo en el taller',               3, '2026-11-17 09:45:00'),
 (1, 'en_proceso',    'en_proceso',   62, 'Carrocería vinilada, falta el techo', 3, '2026-11-21 17:00:00'),
 (5, 'borrador',      'solicitado',    0, 'Solicitud con diseño v2',            10, '2026-11-21 19:20:00'),
 (5, 'solicitado',    'presupuestado', 0, 'Presupuesto enviado',                 2, '2026-11-22 12:30:00'),
 (6, 'listo',         'entregado',   100, 'Vehículo entregado',                  4, '2026-10-15 17:00:00'),
 (7, 'solicitado',    'presupuestado', 0, 'Presupuesto enviado',                 2, '2026-11-23 10:00:00');

-- Diseño IA del pedido 5: v1 y v2 (v2 parte de v1)
INSERT INTO versiones_diseno (pedido_id, numero, version_padre_id, peticion, plan_director, vista_maestra,
                              estado, proveedor_ia, modelo_texto, modelo_imagen, semilla, coste_estimado,
                              creado_por, creado_en, completado_en) VALUES
 (5, 1, NULL, 'Monta este alerón y estas llantas negras.',
     JSON_OBJECT('piezas', JSON_ARRAY('aleron_gt', 'llantas_negras'), 'orden', 'trasera_der primero'),
     'trasera_der', 'listo', 'gemini', 'gemini-2.5-flash', 'gemini-2.5-flash-image', 4211, 0.3120,
     10, '2026-11-21 18:40:00', '2026-11-21 18:44:00'),
 (5, 2, 1, 'El alerón un poco más bajo y más pegado al maletero.',
     JSON_OBJECT('piezas', JSON_ARRAY('aleron_gt'), 'cambio', 'altura_aleron'),
     'trasera_der', 'listo', 'gemini', 'gemini-2.5-flash', 'gemini-2.5-flash-image', 9077, 0.3120,
     10, '2026-11-21 19:05:00', '2026-11-21 19:09:00');

UPDATE pedidos SET version_actual_id = 2, version_aprobada_id = 2 WHERE id = 5;

INSERT INTO version_entradas (version_id, archivo_id, rol, angulo, referencia_id) VALUES
 (1, 1, 'foto_vehiculo',    'frontal_izq', NULL),
 (1, 2, 'foto_vehiculo',    'trasera_der', NULL),
 (1, 3, 'referencia_pieza', NULL,          1),
 (1, 4, 'referencia_pieza', NULL,          2),
 (2, 3, 'referencia_pieza', NULL,          1),
 (2, 4, 'referencia_pieza', NULL,          2);

-- v2 parte de la vista maestra de v1. Se busca por la ruta: tras un INSERT ... SELECT,
-- MySQL puede dejar huecos en los AUTO_INCREMENT y los ids no son predecibles.
INSERT INTO version_entradas (version_id, archivo_id, rol, angulo)
SELECT 2, id, 'version_previa', 'trasera_der' FROM archivos WHERE clave = 'pedidos/5/v1/trasera_der.webp';

INSERT INTO fotogramas_360 (version_id, angulo, archivo_id, origen)
SELECT v.n, a.angulo, ar.id,
       CASE WHEN a.angulo IN ('frontal_izq', 'trasera_der') THEN 'editada' ELSE 'generada' END
FROM (SELECT 1 AS n UNION ALL SELECT 2) v
CROSS JOIN (SELECT 'frontal' AS angulo UNION ALL SELECT 'frontal_izq' UNION ALL SELECT 'lateral_izq'
            UNION ALL SELECT 'trasera_izq' UNION ALL SELECT 'trasera' UNION ALL SELECT 'trasera_der'
            UNION ALL SELECT 'lateral_der' UNION ALL SELECT 'frontal_der') a
JOIN archivos ar ON ar.clave = CONCAT('pedidos/5/v', v.n, '/', a.angulo, '.webp');

INSERT INTO trabajos_ia (tipo, estado, version_id, solicitado_por, proveedor, intentos,
                         creado_en, iniciado_en, finalizado_en) VALUES
 ('diseno_360', 'completado', 1, 10, 'gemini', 1, '2026-11-21 18:40:00', '2026-11-21 18:40:05', '2026-11-21 18:44:00'),
 ('diseno_360', 'completado', 2, 10, 'gemini', 1, '2026-11-21 19:05:00', '2026-11-21 19:05:03', '2026-11-21 19:09:00');

-- Réplica 1:43 impresa del Camry, a partir del diseño v2
INSERT INTO replicas (pedido_id, version_id, cliente_id, formato_entrega, escala, material, acabado,
                      estado, motor_3d, modelo_glb_id, modelo_stl_id, preview_id, precio)
SELECT 5, 2, 10, 'impresion_taller', '1:43', 'PLA', 'pintada', 'modelo_listo', 'trellis',
       (SELECT id FROM archivos WHERE clave = 'replicas/1/modelo.glb'),
       (SELECT id FROM archivos WHERE clave = 'replicas/1/modelo.stl'),
       (SELECT id FROM archivos WHERE clave = 'replicas/1/preview.webp'),
       39.00;

INSERT INTO trabajos_ia (tipo, estado, replica_id, solicitado_por, proveedor, intentos,
                         creado_en, iniciado_en, finalizado_en) VALUES
 ('modelo_3d', 'completado', 1, 10, 'huggingface', 2, '2026-11-22 10:00:00', '2026-11-22 10:00:10', '2026-11-22 10:06:00');

INSERT INTO citas (tipo, pedido_id, empleado_id, titulo, descripcion, inicio, fin, estado, creado_por) VALUES
 ('entrada',  1, 2, 'Entrada BMW M4',            'Recepción del vehículo',      '2026-11-17 09:30:00', '2026-11-17 10:00:00', 'completada', 2),
 ('trabajo',  1, 3, 'Vinilo BMW M4',              'Wrap Satin Black',            '2026-11-24 09:00:00', '2026-11-24 14:00:00', 'programada', 2),
 ('revision', 2, 2, 'Revisión Audi RS3',          'Comprobar piezas recibidas',  '2026-11-24 12:00:00', '2026-11-24 12:30:00', 'programada', 2),
 ('entrega',  4, 4, 'Entrega Porsche 718',        NULL,                          '2026-11-07 17:30:00', '2026-11-07 18:00:00', 'completada', 2),
 ('otro',  NULL, NULL,'Reunión semanal del equipo',NULL,                          '2026-11-24 08:30:00', '2026-11-24 09:00:00', 'programada', 1);

INSERT INTO pagos (concepto, pedido_id, presupuesto_id, replica_id, metodo, importe, estado, referencia, pagado_en) VALUES
 ('pedido',  4, 3, NULL, 'simulado', 816.75, 'completado', 'SIM-7F3A21', '2026-11-07 17:35:00'),
 ('pedido',  1, 1, NULL, 'simulado', 500.00, 'completado', 'SIM-91BC04', '2026-11-11 19:35:00'),
 ('pedido',  6, 5, NULL, 'simulado', 320.65, 'completado', 'SIM-2E77A0', '2026-10-15 17:10:00'),
 ('replica', 5, NULL, 1, 'simulado',  47.19, 'pendiente',  NULL,         NULL);

INSERT INTO valoraciones (pedido_id, cliente_id, puntuacion, comentario) VALUES
 (4, 9, 5, 'Las pinzas han quedado perfectas y me lo entregaron antes de lo previsto.');

INSERT INTO notificaciones (usuario_id, tipo, titulo, cuerpo, pedido_id, pieza_id, replica_id, leida_en) VALUES
 (2,  'solicitud_nueva', 'Nuevo presupuesto solicitado', 'Sergio Mena · Cupra Formentor VZ', 3,    NULL, NULL, NULL),
 (1,  'stock_bajo',      'Stock por debajo del mínimo',  'Kit aerodinámico BMW M4 · 2 uds.',  NULL, 1,    NULL, NULL),
 (10, 'diseno_listo',    'Tu diseño v2 está listo',      'Ya puedes ver el 360 de tu Camry', 5,    NULL, NULL, '2026-11-21 19:12:00'),
 (10, 'presupuesto',     'Tienes un presupuesto',        'Total: 1.851,30 €',                5,    NULL, NULL, NULL),
 (10, 'replica',         'Tu réplica 1:43 está lista para revisar', NULL,                    5,    NULL, 1,    NULL),
 (6,  'estado_pedido',   'Tu BMW M4 va por el 62 %',     'Falta vinilar el techo',          1,    NULL, NULL, NULL);

INSERT INTO preferencias_notificacion (usuario_id, tipo, push, email) VALUES
 (10, 'estado_pedido', TRUE, TRUE),
 (10, 'sistema',       FALSE, FALSE);

INSERT INTO ajustes_taller (clave, valor, actualizado_por) VALUES
 ('general', JSON_OBJECT('nombre', 'Evolve – Rebuild your Ride', 'email', 'taller@evolve.test',
                         'telefono', '+34 910 234 567', 'horario', 'L–V · 08:00–18:00',
                         'direccion', 'Calle del Motor, 24 · Madrid'), 1),
 ('negocio', JSON_OBJECT('razon_social', 'Evolve Customs S.L.', 'nif', 'B00000000',
                         'capacidad_maxima', 10, 'plazo_medio_dias', 5,
                         'direccion_fiscal', 'Calle del Motor, 24 · Madrid'), 1),
 ('avisos_automaticos', JSON_OBJECT('cambios_estado', TRUE, 'confirmacion_presupuestos', TRUE,
                                    'alertas_almacen', TRUE, 'resumen_diario', FALSE), 1),
 ('ia', JSON_OBJECT('generaciones_diarias_por_cliente', 3, 'fotogramas_360', 8,
                    'gasto_maximo_mensual_usd', 8, 'modo_demo', FALSE), 1);

-- =====================================================================
--  CONSULTAS DE COMPROBACIÓN (opcionales)
-- =====================================================================
-- Pedidos con código, cliente y presupuesto:
--   SELECT codigo, cliente, vehiculo, estado, presupuesto_total FROM v_pedidos;
-- Inventario (debe salir KIT-BMW-04 crítico, LLN-19-RS3 bajo, VIN-SAT-BLK correcto 8 uds.):
--   SELECT referencia, stock, stock_minimo, estado_stock FROM v_stock;
-- Spin 360 actual del pedido 5 (8 filas en orden de giro):
--   SELECT angulo, clave FROM v_spin_actual WHERE pedido_id = 5 ORDER BY angulo;
-- Historial de versiones (árbol):
--   SELECT numero, version_padre_id, peticion, estado FROM versiones_diseno WHERE pedido_id = 5;
-- Generaciones de IA de un cliente hoy (límite diario):
--   SELECT COUNT(*) FROM versiones_diseno WHERE creado_por = 10 AND creado_en >= CURDATE();
-- Siguiente trabajo para el worker de IA:
--   START TRANSACTION;
--   SELECT id FROM trabajos_ia WHERE estado = 'en_cola' AND disponible_desde <= NOW()
--    ORDER BY prioridad, id LIMIT 1 FOR UPDATE SKIP LOCKED;
--   UPDATE trabajos_ia SET estado = 'procesando', iniciado_en = NOW(), bloqueado_por = 'worker-1' WHERE id = ?;
--   COMMIT;
-- Prueba del trigger de stock (debe fallar: "Stock insuficiente"):
--   INSERT INTO movimientos_stock (pieza_id, tipo, cantidad) VALUES (5, 'salida_pedido', -5);
-- Prueba de doble reserva (debe fallar: "Franja horaria ocupada"):
--   INSERT INTO citas (tipo, empleado_id, titulo, inicio, fin)
--   VALUES ('trabajo', 3, 'Prueba', '2026-11-24 10:00:00', '2026-11-24 11:00:00');
