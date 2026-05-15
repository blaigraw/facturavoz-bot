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
                INSERT INTO usuarios (chat_id, nombre, nif, direccion, telefono, email, iban, iva, actividad)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET
                    nombre = EXCLUDED.nombre,
                    nif = EXCLUDED.nif,
                    direccion = EXCLUDED.direccion,
                    telefono = EXCLUDED.telefono,
                    email = EXCLUDED.email,
                    iban = EXCLUDED.iban,
                    iva = EXCLUDED.iva,
                    actividad = EXCLUDED.actividad
            """, (chat_id, datos["nombre"], datos["nif"], datos["direccion"], datos["telefono"], datos["email"], datos.get("iban"), datos.get("iva", 0.21), datos.get("actividad")))
        conn.commit()

def cargar_config(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT nombre, nif, direccion, telefono, email, iban, iva, actividad FROM usuarios WHERE chat_id = %s", (chat_id,))
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
                "actividad": row[7]
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
    return f"P-{año}-{numero:03d}"
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
                UPDATE usuarios SET consent_given = %s, consent_date = NOW()
                WHERE chat_id = %s
            """, (tipo != 'no', chat_id))
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