ANGLES = ("frontal", "frontal_izq", "lateral_izq", "trasera_izq",
          "trasera", "trasera_der", "lateral_der", "frontal_der")

ORDER_STATES = ("borrador", "solicitado", "presupuestado", "aceptado", "rechazado", "programado",
                "en_proceso", "esperando_piezas", "listo", "entregado", "cancelado")
FINAL_ORDER_STATES = ("entregado", "cancelado", "rechazado")
ACTIVE_WORK_STATES = ("aceptado", "programado", "en_proceso", "esperando_piezas")
CANCELLABLE_ORDER_STATES = ("borrador", "solicitado", "presupuestado", "aceptado", "programado")

ORDER_TRANSITIONS = {
    "solicitado": {"cancelado"},
    "presupuestado": {"cancelado"},
    "aceptado": {"programado", "en_proceso", "cancelado"},
    "programado": {"en_proceso", "cancelado"},
    "en_proceso": {"en_proceso", "esperando_piezas", "listo"},
    "esperando_piezas": {"en_proceso", "listo"},
    "listo": {"entregado", "en_proceso"},
}

REPLICA_TRANSITIONS = {
    "imprimiendo": {"aprobada"},
    "lista": {"imprimiendo", "aprobada"},
    "entregada": {"lista"},
    "cancelada": {"solicitada", "generando_modelo", "modelo_listo", "aprobada", "error"},
}

NOTIFICATION_TYPES = ("estado_pedido", "presupuesto", "diseno_listo", "replica",
                      "solicitud_nueva", "stock_bajo", "entrega_hoy", "sistema")

PERMISSION_MODULES = ("pedidos", "calendario", "almacen", "cuentas", "equipo", "informes", "configuracion")

REPLICA_PRICES = {
    "archivo_digital": {"default": 9.00},
    "impresion_taller": {"1:64": 19.00, "1:43": 39.00, "1:32": 59.00, "1:24": 89.00, "1:18": 129.00},
}
