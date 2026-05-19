# CLAUDE.md — FacturaVoz

Este archivo es de lectura obligatoria al inicio de cada sesión.
Contiene decisiones cerradas e inviolables. No se modifican sin escalar al HOLDING.

---

## Decisiones inviolables

### 1. Totales siempre en Python, nunca en GPT
El total de la factura/presupuesto se calcula en Python mediante `calcular_subtotal()`
y `calcular_ajuste()` en `bot.py`. GPT extrae campos literales del audio, no opera
sobre números ni calcula sumas. Cualquier código que delegue un cálculo aritmético
a GPT viola esta decisión.

### 2. Numeración fiscal — nunca reiniciar, nunca saltar
- Facturas: formato `AÑO-NNN` (ej: `2026-001`). Función: `get_siguiente_numero_factura()`.
- Presupuestos: formato `P-AÑO-NNNN` (ej: `P-2026-0001`). Función: `get_siguiente_numero_presupuesto()`.
- El correlativo se incrementa en BD con UPDATE atómico. Nunca se reinicia. Nunca se salta.
  Un hueco en la numeración fiscal es un problema legal para el usuario autónomo.
- Al iniciar un documento nuevo (B10): `user_data` empieza limpio. El contador en BD
  (`ultimo_numero_factura`, `ultimo_numero_presupuesto`) NO se toca — solo se incrementa
  en el momento de generar el PDF.

### 3. Webhook siempre, polling nunca
El bot usa `aiohttp` + `set_webhook()`. Está prohibido usar `run_polling()` o
`application.run_polling()` en ningún entorno, incluido desarrollo local.
El Procfile usa `web: python bot.py`. No se cambia.

### 4. B5 — Mano de obra: GPT mapea, no recalcula
GPT extrae el importe de mano de obra literalmente del audio del usuario.
No multiplica horas × precio_hora. No recalcula. El prompt del sistema lo prohíbe
explícitamente. Si el usuario dice "300 euros de mano de obra", GPT devuelve 300.

### 5. Producción es real e irreversible
- Rama `main` + bot `@facturaavoz_bot` = producción real.
- Usuarios reales. Documentos fiscales reales. Base de datos real.
- Todo cambio se desarrolla en rama `dev` y se verifica en staging
  (`@FacturaVozTest_bot`) antes de tocar `main`.
- **Nunca merge a `main` sin staging en verde.**
- Un PDF generado en producción con datos incorrectos es un documento fiscal
  inválido para un autónomo real. No hay Ctrl+Z.

### 6. Datos de test identificables
En la BD real, los registros de prueba llevan apellido "Palotes" o "Testero".
Esto permite distinguirlos de usuarios reales sin necesidad de una BD separada.

---

## Lecciones pagadas — no se vuelven a romper

### L1. ConversationHandler + allow_reentry + VOICE en entry_points
`allow_reentry=True` hace que los entry_points intercepten mensajes mientras
el usuario está mid-flow. Si VOICE está en entry_points (como en este bot), un
audio durante el flujo de registro lo interrumpe. **Nunca añadir `allow_reentry=True`.**

### L2. Orden de handlers dentro de un estado
Dentro de un estado del ConversationHandler, el orden importa:
VOICE antes de TEXT, pattern específico antes de handler genérico.
Un handler genérico declarado antes captura todo lo que debería ir al específico.

### L3. GPT devuelve strings donde se esperan números
GPT puede devolver `"Gratuito"`, `"sin coste"`, `"cincuenta euros"`, `"0,0"`.
Siempre pasar por `to_float()` antes de operar aritméticamente con cualquier
valor que haya pasado por GPT. Nunca asumir que el output es un float.

### L4. GPT devuelve JSON envuelto en markdown
GPT puede devolver ` ```json { ... } ``` ` en lugar de `{ ... }`.
Siempre hacer strip de las marcas de markdown antes de `json.loads()`.
Patrón: `texto.strip().removeprefix("```json").removesuffix("```").strip()`.

### L5. load_dotenv() sobreescribe variables de Railway
`load_dotenv()` sin argumentos sobreescribe las variables de entorno ya definidas.
En Railway, las variables se inyectan en el entorno antes de arrancar el proceso.
Si `load_dotenv()` sobreescribe con valores de un `.env` local que no debería
estar en Railway, las variables correctas de Railway se pierden.
Patrón correcto: `load_dotenv(override=False)`.
**Pendiente de aplicar en `config.py:6`** — no se ha aplicado aún.

### L6. ReportLab Paragraph() solo acepta strings puros
Todo valor que entre en `Paragraph()` de ReportLab debe ser `str`.
Una coma accidental convierte el argumento en tupla y rompe el render del PDF
sin mensaje de error claro. Siempre hacer `str(valor)` antes de pasar a Paragraph.

### L7. Migraciones de BD: siempre IF NOT EXISTS
Patrón correcto: `ALTER TABLE nombre ADD COLUMN IF NOT EXISTS campo tipo`.
Sin `IF NOT EXISTS`, la migración falla si la columna ya existe en producción
(porque se aplicó antes en otro despliegue). Nunca `ADD COLUMN` sin la guarda.

### L8. URL de BD en Railway
- Dentro de Railway: usar `DATABASE_URL` (URL interna, más rápida).
- En local: usar `DATABASE_PUBLIC_URL`.
- No intercambiarlas: la URL interna solo resuelve dentro de la red de Railway.

### L9. ADMIN_CHAT_ID en variables de entorno
`ADMIN_CHAT_ID` debe estar definido en las variables de entorno de Railway.
Si no está, el código lo lee como `0` o `None` y los comandos de administración
no funcionan sin error visible. Verificar que está en Railway antes de depurar
comportamientos de admin.

---

## Entornos

| Entorno | Rama | Bot | BD |
|---|---|---|---|
| Staging | `dev` | `@FacturaVozTest_bot` | Railway (staging) |
| Producción | `main` | `@facturaavoz_bot` | Railway (prod) |

Flujo obligatorio: desarrollar en `dev` → verificar en staging → merge a `main`.
