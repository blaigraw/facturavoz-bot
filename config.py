import os
import json
import psycopg2
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Crea la tabla de usuarios si no existe"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    chat_id BIGINT PRIMARY KEY,
                    nombre TEXT,
                    nif TEXT,
                    direccion TEXT,
                    telefono TEXT,
                    email TEXT,
                    ultimo_numero_factura INTEGER DEFAULT 0,
                    ultimo_numero_presupuesto INTEGER DEFAULT 0
                )
            """)
            cur.execute("""
                ALTER TABLE usuarios
                ADD COLUMN IF NOT EXISTS consent_given BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS consent_date TIMESTAMP
            """)
            cur.execute("""
                ALTER TABLE usuarios
                ADD COLUMN IF NOT EXISTS iban TEXT
            """)
            cur.execute("""
                ALTER TABLE usuarios
                ADD COLUMN IF NOT EXISTS iva FLOAT DEFAULT 0.21
            """)
            cur.execute("""
                ALTER TABLE usuarios
                ADD COLUMN IF NOT EXISTS actividad TEXT
            """)
            cur.execute("""
                ALTER TABLE usuarios
                ADD COLUMN IF NOT EXISTS pruebas_realizadas INTEGER DEFAULT 0
            """)
            cur.execute("""
                ALTER TABLE usuarios
                ADD COLUMN IF NOT EXISTS precio_hora FLOAT DEFAULT NULL
            """)
            cur.execute("""
                ALTER TABLE usuarios
                ADD COLUMN IF NOT EXISTS mostrar_precio_hora BOOLEAN DEFAULT TRUE
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    transcripcion TEXT,
                    tipo_detectado TEXT,
                    json_estructurado JSONB,
                    tipo_final TEXT,
                    campos_editados TEXT[],
                    numero_ediciones INTEGER DEFAULT 0,
                    numero_documento TEXT,
                    total_factura FLOAT,
                    concepto TEXT,
                    accion_final TEXT,
                    segundos_hasta_confirmacion INTEGER
                )
            """)
        conn.commit()

def config_existe(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM usuarios WHERE chat_id = %s", (chat_id,))
            return cur.fetchone() is not None

def guardar_config(chat_id, datos):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO usuarios (chat_id, nombre, nif, direccion, telefono, email, iban, iva, actividad, precio_hora, mostrar_precio_hora)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET
                    nombre = EXCLUDED.nombre,
                    nif = EXCLUDED.nif,
                    direccion = EXCLUDED.direccion,
                    telefono = EXCLUDED.telefono,
                    email = EXCLUDED.email,
                    iban = EXCLUDED.iban,
                    iva = EXCLUDED.iva,
                    actividad = EXCLUDED.actividad,
                    precio_hora = EXCLUDED.precio_hora,
                    mostrar_precio_hora = EXCLUDED.mostrar_precio_hora
            """, (chat_id, datos["nombre"], datos["nif"], datos["direccion"], datos["telefono"], datos["email"], datos.get("iban"), datos.get("iva", 0.21), datos.get("actividad"), datos.get("precio_hora"), datos.get("mostrar_precio_hora", True)))
            cur.execute("""
                UPDATE usuarios SET consent_given = TRUE, consent_date = NOW()
                WHERE chat_id = %s AND consent_given IS NULL
            """, (chat_id,))
        conn.commit()

def cargar_config(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT nombre, nif, direccion, telefono, email, iban, iva, actividad, precio_hora, mostrar_precio_hora FROM usuarios WHERE chat_id = %s", (chat_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "nombre": row[0],
                "nif": row[1],
                "direccion": row[2],
                "telefono": row[3],
                "email": row[4],
                "iban": row[5],
                "iva": row[6],
                "actividad": row[7],
                "precio_hora": row[8],
                "mostrar_precio_hora": row[9] if row[9] is not None else True
            }

def get_siguiente_numero_factura(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE usuarios SET ultimo_numero_factura = ultimo_numero_factura + 1
                WHERE chat_id = %s
                RETURNING ultimo_numero_factura
            """, (chat_id,))
            numero = cur.fetchone()[0]
        conn.commit()
    año = datetime.now().year
    return f"{año}-{numero:03d}"

def get_siguiente_numero_presupuesto(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE usuarios SET ultimo_numero_presupuesto = ultimo_numero_presupuesto + 1
                WHERE chat_id = %s
                RETURNING ultimo_numero_presupuesto
            """, (chat_id,))
            numero = cur.fetchone()[0]
        conn.commit()
    año = datetime.now().year
    return f"P-{año}-{numero:04d}"

def guardar_numero_inicial_factura(chat_id, numero: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE usuarios SET ultimo_numero_factura = %s WHERE chat_id = %s
            """, (numero, chat_id))
        conn.commit()

def guardar_numero_inicial_presupuesto(chat_id, numero: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE usuarios SET ultimo_numero_presupuesto = %s WHERE chat_id = %s
            """, (numero, chat_id))
        conn.commit()

def guardar_log(chat_id, datos):
    """Guarda un log de interacción"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO logs (
                        chat_id, transcripcion, tipo_detectado,
                        json_estructurado, tipo_final, campos_editados,
                        numero_ediciones, numero_documento, total_factura,
                        concepto, accion_final, segundos_hasta_confirmacion
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    chat_id,
                    datos.get("transcripcion"),
                    datos.get("tipo_detectado"),
                    json.dumps(datos.get("json_estructurado")),
                    datos.get("tipo_final"),
                    datos.get("campos_editados", []),
                    datos.get("numero_ediciones", 0),
                    datos.get("numero_documento"),
                    datos.get("total_factura"),
                    datos.get("concepto"),
                    datos.get("accion_final"),
                    datos.get("segundos_hasta_confirmacion")
                ))
            conn.commit()
    except Exception as e:
        print(f"Error guardando log: {e}")
def guardar_consentimiento(chat_id, tipo):
    """Guarda el consentimiento del usuario. tipo: 'completo', 'basico', 'no'"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO usuarios (chat_id, consent_given, consent_date)
                VALUES (%s, %s, NOW())
                ON CONFLICT (chat_id) DO UPDATE
                SET consent_given = EXCLUDED.consent_given,
                    consent_date = EXCLUDED.consent_date
            """, (chat_id, tipo != 'no'))
        conn.commit()

def tiene_consentimiento(chat_id):
    """Comprueba si el usuario ya dio consentimiento explícito"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT consent_given FROM usuarios WHERE chat_id = %s", (chat_id,))
            row = cur.fetchone()
            if not row:
                return False
            return row[0] is True  # Solo True explícito, no NULL

def guardar_iban(chat_id, iban):
    """Guarda o actualiza el IBAN del autónomo"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE usuarios SET iban = %s WHERE chat_id = %s
            """, (iban, chat_id))
        conn.commit()

def guardar_iva(chat_id, iva):
    """Guarda o actualiza el IVA habitual del autónomo"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE usuarios SET iva = %s WHERE chat_id = %s", (iva, chat_id))
        conn.commit()

def get_pruebas_realizadas(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pruebas_realizadas FROM usuarios WHERE chat_id = %s", (chat_id,))
            row = cur.fetchone()
            if not row:
                return 0
            return row[0] or 0

def incrementar_prueba(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO usuarios (chat_id, pruebas_realizadas)
                VALUES (%s, 1)
                ON CONFLICT (chat_id) DO UPDATE
                SET pruebas_realizadas = usuarios.pruebas_realizadas + 1
            """, (chat_id,))
        conn.commit()

def get_user_exists(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM usuarios WHERE chat_id = %s", (chat_id,))
            return cur.fetchone() is not None

def eliminar_usuario(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM usuarios WHERE chat_id = %s", (chat_id,))
        conn.commit()

def eliminar_logs(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM logs WHERE chat_id = %s", (chat_id,))
        conn.commit()

def crear_tablas_mantenimiento():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    clave VARCHAR(100) PRIMARY KEY,
                    valor VARCHAR(500)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS notificaciones_pendientes (
                    chat_id BIGINT PRIMARY KEY
                )
            """)
        conn.commit()

def get_mantenimiento():
    env_val = os.getenv("MAINTENANCE_MODE", "").lower()
    if env_val == "true":
        return True
    if env_val == "false":
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT valor FROM config WHERE clave = 'maintenance_mode'")
            row = cur.fetchone()
            return row is not None and row[0] == "true"

def set_mantenimiento(activo: bool):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO config (clave, valor) VALUES ('maintenance_mode', %s)
                ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor
            """, ("true" if activo else "false",))
        conn.commit()

def add_notificacion_pendiente(chat_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO notificaciones_pendientes (chat_id) VALUES (%s)
                ON CONFLICT DO NOTHING
            """, (chat_id,))
        conn.commit()

def get_notificaciones_pendientes():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM notificaciones_pendientes")
            return [row[0] for row in cur.fetchall()]

def vaciar_notificaciones_pendientes():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM notificaciones_pendientes")
        conn.commit()