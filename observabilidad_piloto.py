#!/usr/bin/env python3
"""
observabilidad_piloto.py — FacturaVoz
Solo lectura. No modifica nada en la BD.

Uso:
    DATABASE_PUBLIC_URL=postgres://... python observabilidad_piloto.py

O con .env local que tenga DATABASE_PUBLIC_URL definido.
"""
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(override=False)  # L5: no sobreescribir variables de Railway

try:
    import psycopg2
    from tabulate import tabulate
except ImportError:
    print("Faltan dependencias. Ejecuta: pip install psycopg2-binary tabulate")
    sys.exit(1)

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_PUBLIC_URL no está definido en el entorno.")
    sys.exit(1)

# ─── Consulta principal ────────────────────────────────────────────────────────

QUERY_USUARIOS = """
SELECT
    u.chat_id,
    TO_CHAR(u.consent_date, 'DD/MM/YYYY')                          AS fecha_alta,
    COUNT(l.id) FILTER (WHERE l.tipo_final = 'factura')            AS facturas,
    COUNT(l.id) FILTER (WHERE l.tipo_final = 'presupuesto')        AS presupuestos,
    TO_CHAR(MAX(l.timestamp), 'DD/MM/YYYY')                        AS ultima_actividad,
    ROUND(AVG(l.numero_ediciones)::numeric, 1)                     AS audios_por_sesion,
    ROUND((u.iva * 100)::numeric, 0)::text || '%'                  AS iva,
    CASE u.consent_given WHEN TRUE THEN 'Sí' ELSE 'No' END         AS consentimiento,
    CASE u.mostrar_precio_hora WHEN TRUE THEN 'Sí' ELSE 'No' END   AS muestra_horas
FROM usuarios u
LEFT JOIN logs l ON l.chat_id = u.chat_id
WHERE u.nombre NOT ILIKE '%Palotes%'
  AND u.nombre NOT ILIKE '%Testero%'
GROUP BY u.chat_id, u.consent_date, u.iva, u.consent_given, u.mostrar_precio_hora
ORDER BY ultima_actividad DESC NULLS LAST
"""

QUERY_CAMPOS_EDITADOS = """
SELECT
    l.chat_id,
    campo,
    COUNT(*) AS veces
FROM logs l
CROSS JOIN UNNEST(l.campos_editados) AS campo
JOIN usuarios u ON u.chat_id = l.chat_id
WHERE u.nombre NOT ILIKE '%Palotes%'
  AND u.nombre NOT ILIKE '%Testero%'
  AND l.campos_editados IS NOT NULL
GROUP BY l.chat_id, campo
ORDER BY l.chat_id, veces DESC
"""

# ─── Ejecución ─────────────────────────────────────────────────────────────────

def main():
    try:
        conn = psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"ERROR al conectar con la BD: {e}")
        sys.exit(1)

    print(f"\n=== FacturaVoz · Observabilidad piloto · {datetime.now().strftime('%d/%m/%Y %H:%M')} ===\n")

    with conn.cursor() as cur:

        # ── Tabla principal por usuario ──
        cur.execute(QUERY_USUARIOS)
        rows = cur.fetchall()

        if not rows:
            print("No hay usuarios reales en la BD todavía.\n")
            conn.close()
            return

        headers = [
            "chat_id", "Alta", "Facturas", "Presupuestos",
            "Última actividad", "Audios/sesión", "IVA",
            "Consentimiento", "Muestra horas",
            "Errores"          # campo 8 — no disponible
        ]

        tabla = []
        for row in rows:
            tabla.append(list(row) + ["— n/d"])   # campo 8 siempre n/d

        print(tabulate(tabla, headers=headers, tablefmt="simple"))

        # ── Resumen ──
        total = len(rows)
        activos = sum(1 for r in rows if (r[2] or 0) + (r[3] or 0) > 0)
        sin_doc = total - activos

        print(f"\nUsuarios con al menos 1 documento generado : {activos} / {total}")
        print(f"Usuarios registrados sin ningún documento  : {sin_doc} / {total}")

        # ── Campos más editados por usuario ──
        cur.execute(QUERY_CAMPOS_EDITADOS)
        ediciones = cur.fetchall()

        if ediciones:
            print("\n── Campos editados tras transcripción (por usuario) ──\n")
            por_usuario = {}
            for chat_id, campo, veces in ediciones:
                por_usuario.setdefault(chat_id, []).append(f"{campo} ({veces}x)")

            edicion_tabla = [
                [chat_id, ", ".join(campos[:5])]   # máx 5 campos por usuario
                for chat_id, campos in por_usuario.items()
            ]
            print(tabulate(edicion_tabla, headers=["chat_id", "Campos editados"], tablefmt="simple"))

        # ── Detección de tipo: aciertos vs correcciones ──
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE tipo_detectado = tipo_final)  AS aciertos,
                COUNT(*) FILTER (WHERE tipo_detectado != tipo_final
                                   AND tipo_detectado IS NOT NULL
                                   AND tipo_final IS NOT NULL)        AS correcciones,
                COUNT(*)                                               AS total
            FROM logs l
            JOIN usuarios u ON u.chat_id = l.chat_id
            WHERE u.nombre NOT ILIKE '%Palotes%'
              AND u.nombre NOT ILIKE '%Testero%'
        """)
        fila = cur.fetchone()
        if fila and fila[2]:
            aciertos, correcciones, total_logs = fila
            print(f"\n── Detección automática factura/presupuesto ──\n")
            print(f"  GPT acertó el tipo : {aciertos} / {total_logs}")
            print(f"  Usuario corrigió   : {correcciones} / {total_logs}")

    print()
    conn.close()

if __name__ == "__main__":
    main()
