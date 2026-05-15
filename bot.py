import os
import json
from datetime import datetime
from config import config_existe, guardar_config, cargar_config, get_siguiente_numero_factura, get_siguiente_numero_presupuesto, init_db, guardar_log, guardar_consentimiento, tiene_consentimiento, guardar_iban, guardar_iva
from holded import crear_factura
from factura_pdf import generar_factura_pdf
from openai import OpenAI
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# Carga variables de entorno del archivo .env
load_dotenv(override=False)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Estados de la conversación
ESPERANDO_AUDIO = 0
ESPERANDO_CONFIRMACION = 1
ESPERANDO_VALOR_CAMPO = 2
REGISTRO_NOMBRE = 3
REGISTRO_NIF = 4
REGISTRO_DIRECCION = 5
REGISTRO_TELEFONO = 6
REGISTRO_EMAIL = 7
ESPERANDO_CONSENTIMIENTO = 8
ESPERANDO_IBAN = 9

def get_prompt_sistema():
    """Genera el prompt con la fecha de hoy actualizada"""
    hoy = datetime.now().strftime("%d/%m/%Y")
    return f"""
Eres un asistente para autónomos del sector construcción en España.
Recibirás una transcripción de una nota de voz informal.
Tu trabajo es extraer los datos para generar una factura o presupuesto.

La fecha de hoy es {hoy}. Usa esta fecha por defecto si no se menciona otra.

Devuelve SOLO un JSON válido con estos campos exactos:
{{
    "tipo": "factura o presupuesto según lo que mencione el audio. Si no se menciona, usa null",
    "cliente_nombre": "nombre de la persona o empresa",
    "cliente_direccion": "dirección completa del cliente",
    "concepto": "descripción del trabajo realizado",
    "materiales": [{{"descripcion": "...", "precio": 0.00}}],
    "horas": 0,
    "precio_hora": 0.00,
    "desplazamiento": 0.00,
    "validez_dias": null,
    "fecha": "{hoy}",
    "total": 0.00
}}

Reglas:
- Si un dato no aparece, ponlo como null
- Ordena los datos y la informacion de la manera mas comoda para leer e introducir en una factura, con signos de puntuacion.
 Estructura la direccion como sereia la manera legal y aceptada por instituciones.
 Usa abreviaciones estándar españolas: C/ para Calle, Avda. para Avenida, Pza. para Plaza, Ctra. para Carretera.
 Ejemplo: "C/ Virgen del Pilar, 34, 3ºA, 28820 Coslada, Madrid"
- El campo tipo solo puede ser "factura" o "presupuesto" o null
- Si el audio menciona palabras como "presupuesto", "precio aproximado", "cuánto costaría" → tipo: "presupuesto"
- Si el audio menciona "factura", "cobrar", "trabajo terminado" → tipo: "factura"
- Si tipo es "presupuesto", validez_dias por defecto es 30 salvo que se indique otro
- Si tipo es "factura", validez_dias es null
- En el concepto escribe solo la primera letra en mayúscula y el resto en minúscula, excepto nombres propios y marcas comerciales
- En los materiales capitaliza solo la primera letra de la descripción y los nombres de marcas comerciales (ej: "Grifo Roca", "Portero Fermax")
- El total es la suma de materiales + (horas x precio_hora) + desplazamiento
- La fecha de hoy es {hoy} — úsala si no se menciona otra fecha, si se menciona una fecha concreta calcula la fecha exacta en formato DD/MM/YYYY partiendo de que hoy es {hoy}
- Si se menciona una fecha relativa como 'ayer', 'la semana pasada', 'el lunes pasado', calcula la fecha exacta en formato DD/MM/YYYY partiendo de que hoy es {hoy}
- Devuelve SOLO el JSON, sin texto adicional ni bloques de código
"""

def construir_resumen(datos, iva_porcentaje=0.21):
    """Construye el texto del resumen — factura o presupuesto"""
    total_horas = (datos["horas"] or 0) * (datos["precio_hora"] or 0)
    tipo = datos.get("tipo", "factura")
    
    if tipo == "presupuesto":
        titulo = "📋 *Resumen del presupuesto:*"
    else:
        titulo = "✅ *Resumen de la factura:*"

    resumen = (
        f"{titulo}\n\n"
        f"👤 *Cliente:* {datos.get('cliente_nombre') or 'No especificado'}\n"
        f"📍 *Dirección:* {datos.get('cliente_direccion') or 'No especificada'}\n"
        f"🔧 *Trabajo:* {datos['concepto'] or 'No especificado'}\n"
    )

    if datos["materiales"]:
        resumen += "\n📦 *Materiales:*\n"
        for m in datos["materiales"]:
            resumen += f"  • {m['descripcion']}: {m['precio']}€\n"

    resumen += (
        f"\n⏱️ *Horas:* {datos['horas'] or 0}h x {datos['precio_hora'] or 0}€ = {total_horas}€\n"
        f"🚗 *Desplazamiento:* {datos['desplazamiento'] or 0}€\n"
        f"📅 *Fecha:* {datos['fecha'] or 'No especificada'}\n"
    )

    if tipo == "presupuesto":
        validez = datos.get("validez_dias") or 30
        resumen += f"⏳ *Validez:* {validez} días\n"

    iva_etiqueta = f"{int(iva_porcentaje * 100)}%" if iva_porcentaje > 0 else "Exento"
    resumen += (
        f"\n💰 *Subtotal: {datos['total'] or 0}€*\n"
        f"🧾 *IVA ({iva_etiqueta}): {round((datos['total'] or 0) * iva_porcentaje, 2)}€*\n"
        f"💵 *TOTAL: {round((datos['total'] or 0) * (1 + iva_porcentaje), 2)}€*\n\n"
        f"¿Es correcto?"
    )
    return resumen

def construir_teclado_confirmacion(tipo="factura"):
    if tipo == "presupuesto":
        texto_confirmar = "✅ SÍ, crear presupuesto"
        callback_confirmar = "confirmar_presupuesto"
        texto_cambiar = "📄 Cambiar a factura"
        callback_cambiar = "cambiar_tipo_factura"
    else:
        texto_confirmar = "✅ SÍ, crear factura"
        callback_confirmar = "confirmar_factura"
        texto_cambiar = "📋 Cambiar a presupuesto"
        callback_cambiar = "cambiar_tipo_presupuesto"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(texto_confirmar, callback_data=callback_confirmar),
            InlineKeyboardButton("✏️ Editar campo", callback_data="editar"),
        ],
        [
            InlineKeyboardButton(texto_cambiar, callback_data=callback_cambiar),
            InlineKeyboardButton("❌ Repetir audio", callback_data="repetir")
        ]
    ])

def construir_teclado_campos():
    """Construye los botones de selección de campo a editar"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 Cliente", callback_data="campo_cliente_nombre"),
            InlineKeyboardButton("📍 Dirección", callback_data="campo_cliente_direccion"),
            InlineKeyboardButton("🔧 Trabajo", callback_data="campo_concepto")
        ],
        [
            InlineKeyboardButton("📦 Materiales", callback_data="campo_materiales"),
            InlineKeyboardButton("⏱️ Horas", callback_data="campo_horas")
        ],
        [
            InlineKeyboardButton("💰 Precio/hora", callback_data="campo_precio_hora"),
            InlineKeyboardButton("🚗 Desplazamiento", callback_data="campo_desplazamiento")
        ],
        [
            InlineKeyboardButton("📅 Fecha", callback_data="campo_fecha"),
            InlineKeyboardButton("💵 Total", callback_data="campo_total")
        ]
     ])
async def pedir_consentimiento(update: Update):
    """Muestra el mensaje de consentimiento RGPD"""
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Acepto todo", callback_data="consent_completo"),
            InlineKeyboardButton("📋 Solo lo básico", callback_data="consent_basico"),
        ],
        [
            InlineKeyboardButton("🚫 No acepto", callback_data="consent_no")
        ]
    ])
    await update.message.reply_text(
        "👋 Bienvenido a *FacturaVoz*\n\n"
        "Para funcionar proceso tus notas de voz y genero facturas en PDF.\n\n"
        "Opcionalmente, y solo con tu permiso, guardo las transcripciones "
        "para mejorar el servicio.\n\n"
        "📄 Usa /privacidad para más información.\n\n"
        "¿Aceptas las condiciones?",
        parse_mode="Markdown",
        reply_markup=teclado
    )

async def handle_consentimiento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestiona la respuesta al consentimiento RGPD"""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if query.data == "consent_no":
        await query.edit_message_text(
            "🚫 Sin tu consentimiento no puedo continuar.\n"
            "Usa /start cuando quieras reconsiderarlo."
        )
        return ConversationHandler.END

    tipo = "completo" if query.data == "consent_completo" else "basico"
    guardar_consentimiento(chat_id, tipo)

    await query.edit_message_text(
        "✅ Gracias. Ya puedes empezar a usar FacturaVoz.\n\n"
        "Envíame una nota de voz describiendo el trabajo."
    )
    return ESPERANDO_AUDIO
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start — comprueba registro y consentimiento"""
    context.user_data.clear()
    chat_id = update.effective_chat.id

    if config_existe(chat_id):
        config = cargar_config(chat_id)
        texto = (
            f"👋 Hola de nuevo, {config['nombre']}.\n\n"
            "Envíame una nota de voz describiendo el trabajo y te generaré "
            "una *factura* o *presupuesto* al instante.\n\n"
            "Puedes decir por ejemplo:\n"
            "• _'Hazme una factura para Juan García...'_\n"
            "• _'Necesito un presupuesto para una reforma...'_\n\n"
            "Intenta incluir:\n"
            "• Nombre y dirección del cliente\n"
            "• Trabajo realizado\n"
            "• Materiales usados y su precio\n"
            "• Horas trabajadas y precio por hora\n"
            "• Desplazamiento si lo hay\n"
            "• Fecha del trabajo"
        )
        await update.message.reply_text(texto, parse_mode="Markdown")
        return ESPERANDO_AUDIO
    else:
        await update.message.reply_text(
            "👋 Hola, soy tu asistente de facturas.\n\n"
            "Antes de empezar necesito configurar tus datos para las facturas.\n\n"
            "¿Cuál es tu nombre completo o razón social?"
        )
        return REGISTRO_NOMBRE

async def registro_nombre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el nombre del autónomo"""
    context.user_data["reg_nombre"] = update.message.text
    await update.message.reply_text(
        f"✅ Perfecto, {update.message.text}.\n\n"
        "¿Cuál es tu NIF o CIF?"
    )
    return REGISTRO_NIF

async def registro_nif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el NIF del autónomo"""
    context.user_data["reg_nif"] = update.message.text.upper()
    await update.message.reply_text(
        "¿Cuál es tu dirección completa?\n"
        "Ejemplo: Calle Mayor 1, 08001 Barcelona"
    )
    return REGISTRO_DIRECCION

async def registro_direccion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe la dirección del autónomo"""
    context.user_data["reg_direccion"] = update.message.text
    await update.message.reply_text("¿Cuál es tu teléfono de contacto?")
    return REGISTRO_TELEFONO

async def registro_telefono(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el teléfono del autónomo"""
    context.user_data["reg_telefono"] = update.message.text
    await update.message.reply_text("¿Cuál es tu email?")
    return REGISTRO_EMAIL

async def registro_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el email y guarda toda la configuración"""
    config = {
        "nombre": context.user_data["reg_nombre"],
        "nif": context.user_data["reg_nif"],
        "direccion": context.user_data["reg_direccion"],
        "telefono": context.user_data["reg_telefono"],
        "email": update.message.text
    }
    chat_id = update.effective_chat.id
    guardar_config(chat_id, config)
    await update.message.reply_text(
        f"✅ *Configuración guardada correctamente.*\n\n"
        f"👤 *Nombre:* {config['nombre']}\n"
        f"🪪 *NIF:* {config['nif']}\n"
        f"📍 *Dirección:* {config['direccion']}\n"
        f"📞 *Teléfono:* {config['telefono']}\n"
        f"📧 *Email:* {config['email']}\n\n"
        "Ya puedes empezar a crear facturas y presupuestos.\n\n"
        "Envíame una nota de voz describiendo el trabajo y te generaré "
        "una *factura* o *presupuesto* al instante.\n\n"
        "Puedes decir por ejemplo:\n"
        "• _'Hazme una factura para Juan García, dirección Calle Mayor 1, "
        "reparación de tubería, 3 horas a 35 euros/h, desplazamiento 20 euros'_\n\n"
        "• _'Necesito un presupuesto para Maria López, reforma de cocina, "
        "materiales 500 euros, 8 horas a 30 euros'_\n\n"
        "Intenta incluir:\n"
        "• Nombre y dirección del cliente\n"
        "• Trabajo realizado\n"
        "• Materiales usados y su precio\n"
        "• Horas trabajadas y precio por hora\n"
        "• Desplazamiento si lo hay\n"
        "• Fecha del trabajo\n\n"
        "💡 Tu IVA habitual está configurado al 21%. "
        "Puedes cambiarlo en cualquier momento con /perfil.",
        parse_mode="Markdown"
    )
    return ESPERANDO_AUDIO

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el audio, transcribe con Whisper y estructura con ChatGPT"""
    await update.message.reply_text("🎙️ Audio recibido, transcribiendo...")

    # Descarga el audio desde los servidores de Telegram
    file = await context.bot.get_file(update.message.voice.file_id)
    audio_path = "audio.ogg"
    await file.download_to_drive(audio_path)

    # Transcribe con Whisper
    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="es"
        )

    texto = transcription.text
    print(f"Transcripción: {texto}")
    await update.message.reply_text(
        f"📝 He entendido:\n_{texto}_",
        parse_mode="Markdown"
    )
    await update.message.reply_text("⚙️ Estructurando datos...")

    # Envía la transcripción a ChatGPT para estructurarla
    respuesta = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": get_prompt_sistema()},
            {"role": "user", "content": texto}
        ]
    )

    try:
        contenido = respuesta.choices[0].message.content.strip()
        contenido = contenido.replace("```json", "").replace("```", "").strip()
        datos = json.loads(contenido)
        total_materiales = sum(m["precio"] for m in datos["materiales"]) if datos["materiales"] else 0
        total_horas = (datos["horas"] or 0) * (datos["precio_hora"] or 0)
        total_desplazamiento = datos["desplazamiento"] or 0
        datos["total"] = round(total_materiales + total_horas + total_desplazamiento, 2)
        context.user_data["datos_factura"] = datos
        context.user_data["transcripcion"] = texto
        context.user_data["tipo_detectado"] = datos.get("tipo")
        context.user_data["campos_editados"] = []
        context.user_data["numero_ediciones"] = 0
        context.user_data["tiempo_inicio"] = datetime.now().timestamp()
        tipo = datos.get("tipo")

        # Punto 5 — fallback si GPT no detectó el tipo
        if not tipo:
            teclado_tipo = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📄 Factura", callback_data="tipo_factura"),
                    InlineKeyboardButton("📋 Presupuesto", callback_data="tipo_presupuesto")
                ]
            ])
            await update.message.reply_text(
                "🤖 No he entendido si es una factura o un presupuesto.\n"
                "¿Qué documento necesitas?",
                reply_markup=teclado_tipo
            )
            return ESPERANDO_CONFIRMACION

        # Flujo normal si tipo detectado
        await update.message.reply_text(
            construir_resumen(datos),
            parse_mode="Markdown",
            reply_markup=construir_teclado_confirmacion(tipo)
        )
        return ESPERANDO_CONFIRMACION

    except json.JSONDecodeError:
        await update.message.reply_text(
            "❌ No he podido estructurar los datos. Por favor, envía otra nota de voz."
        )
        return ESPERANDO_AUDIO

async def handle_confirmacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestiona todos los botones inline del flujo de confirmación"""
    query = update.callback_query
    await query.answer()
    if query.data in ("tipo_factura", "tipo_presupuesto"):
        datos = context.user_data.get("datos_factura")
        datos["tipo"] = "factura" if query.data == "tipo_factura" else "presupuesto"
        if datos["tipo"] == "presupuesto":
            datos["validez_dias"] = 30
        context.user_data["datos_factura"] = datos
        tipo = datos["tipo"]
        await query.edit_message_text(
            construir_resumen(datos),
            parse_mode="Markdown",
            reply_markup=construir_teclado_confirmacion(tipo)
        )
        return ESPERANDO_CONFIRMACION
    elif query.data in ("cambiar_tipo_factura", "cambiar_tipo_presupuesto"):
            datos = context.user_data.get("datos_factura")
            datos["tipo"] = "factura" if query.data == "cambiar_tipo_factura" else "presupuesto"
            datos["validez_dias"] = 30 if datos["tipo"] == "presupuesto" else None
            context.user_data["datos_factura"] = datos
            tipo = datos["tipo"]
            await query.edit_message_text(
                construir_resumen(datos),
                parse_mode="Markdown",
                reply_markup=construir_teclado_confirmacion(tipo)
            )
            return ESPERANDO_CONFIRMACION
    elif query.data in ("confirmar_factura", "confirmar_presupuesto"):
        datos = context.user_data.get("datos_factura")
        tipo = datos.get("tipo", "factura")

        if tipo == "factura":
            config = cargar_config(query.message.chat_id)
            teclado_iban = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("💳 Con IBAN", callback_data="iban_si"),
                    InlineKeyboardButton("⏭️ Sin IBAN", callback_data="iban_no")
                ]
            ])
            context.user_data["pendiente_confirmar"] = True
            await query.message.reply_text(
                f"💳 ¿Incluir IBAN en la factura?\n"
                f"{'IBAN guardado: ' + config['iban'][:8] + '...' if config.get('iban') else 'No tienes IBAN configurado.'}",
                reply_markup=teclado_iban
            )
            return ESPERANDO_CONFIRMACION

        await generar_y_enviar_pdf(query, context)
        return ESPERANDO_CONFIRMACION

    elif query.data == "iban_no":
        context.user_data["usar_iban"] = False
        await generar_y_enviar_pdf(query, context)
        return ESPERANDO_CONFIRMACION

    elif query.data == "iban_si":
        config = cargar_config(query.message.chat_id)
        if config.get("iban"):
            context.user_data["usar_iban"] = True
            await generar_y_enviar_pdf(query, context)
            return ESPERANDO_CONFIRMACION
        else:
            await query.message.reply_text(
                "💳 Escribe tu IBAN:\n"
                "Ejemplo: ES91 2100 0418 4200 0512 3456"
            )
            return ESPERANDO_IBAN

    elif query.data == "editar":
        await query.message.reply_text(
            "✏️ *¿Qué campo quieres corregir?*",
            parse_mode="Markdown",
            reply_markup=construir_teclado_campos()
        )
        return ESPERANDO_CONFIRMACION

    elif query.data.startswith("campo_"):
        campo = query.data.replace("campo_", "")
        context.user_data["campo_editando"] = campo
        nombres = {
            "cliente_nombre": "nombre del cliente",
            "cliente_direccion": "dirección del cliente",
            "concepto": "trabajo realizado",
            "materiales": "materiales",
            "horas": "número de horas",
            "precio_hora": "precio por hora",
            "desplazamiento": "desplazamiento en €",
            "fecha": "fecha",
            "total": "total en €"
        }
        await query.message.reply_text(
            f"✏️ *{nombres[campo].capitalize()}*: envía un audio o escríbelo directamente.\n/cancelar para salir.",
            parse_mode="Markdown"
        )
        return ESPERANDO_VALOR_CAMPO

    elif query.data == "repetir":
        await query.edit_message_text(
            "🔄 Sin problema. Envía una nueva nota de voz con los datos del trabajo."
        )
        return ESPERANDO_AUDIO

    elif query.data == "nueva_factura":
        await query.edit_message_text(
            "👋 Envíame una nota de voz describiendo el trabajo y te generaré "
            "una *factura* o *presupuesto* al instante.\n\n"
            "Puedes decir por ejemplo:\n"
            "• _'Hazme una factura para Juan García...'_\n"
            "• _'Necesito un presupuesto para una reforma...'_\n\n"
            "Intenta incluir:\n"
            "• Nombre del cliente\n"
            "• Dirección \n"
            "• Trabajo realizado\n"
            "• Materiales usados y su precio\n"
            "• Horas trabajadas y precio por hora\n"
            "• Desplazamiento si lo hay\n"
            "• Fecha del trabajo"
        )
        return ESPERANDO_AUDIO

async def interpretar_campo_con_gpt(campo, valor_texto, datos_actuales):
    """Interpreta lenguaje natural para un campo y devuelve el valor correcto"""
    hoy = datetime.now().strftime("%d/%m/%Y")

    prompt = f"""
Eres un asistente que interpreta texto informal de autónomos españoles
del sector construcción para actualizar campos de una factura.

Campo a actualizar: {campo}
Texto del usuario: "{valor_texto}"
Datos actuales de la factura: {json.dumps(datos_actuales, ensure_ascii=False)}
Fecha de hoy: {hoy}

Devuelve SOLO un JSON con el campo actualizado. Ejemplos por campo:

- horas: {{"horas": 4.5}}  ← interpreta "cuatro y media", "4h30", "unas 5 horas"
- precio_hora: {{"precio_hora": 35.0}}  ← interpreta "a 35 la hora", "35€/h"
- desplazamiento: {{"desplazamiento": 20.0}}  ← interpreta "20 de gasolina", "veinte euros"
- fecha: {{"fecha": "15/05/2025"}}  ← interpreta "ayer", "el lunes", "15 de mayo"
- cliente_nombre: {{"cliente_nombre": "Juan García López"}}  ← capitaliza correctamente
- cliente_direccion: {{"cliente_direccion": "Calle Mayor, 1, 28001 Madrid"}}  ← formato postal
- concepto: {{"concepto": "Reparación de tubería de agua fría"}}  ← primera letra mayúscula
- materiales: {{"materiales": [{{"descripcion": "Grifo Roca", "precio": 45.0}}]}}
  Para materiales, interpreta lenguaje como:
  "añade un grifo Roca por 45 euros" → añade a los materiales existentes
  "quita el primero" → elimina el primer material
  "cambia todo: tubería PVC 12 euros, cinta teflón 2 euros" → reemplaza todos
  "un grifo 45, tubería 12, cinta 2" → reemplaza todos

Además de interpretar el valor, aplica siempre estas correcciones:
- Corrige errores ortográficos y de acentuación
- Nombres propios con primera letra en mayúscula (personas, empresas, calles)
- Marcas comerciales con su capitalización correcta (Roca, Grohe, Schneider, Legrand, Fermax, Baxi, Vaillant, etc.)
- Direcciones en formato postal español: "Calle/Avenida/Plaza Nombre, número, piso, CP Municipio, Provincia". Solo incluye el código postal si estás seguro al 100%. Si hay duda, omítelo y deja la dirección sin CP. Usa abreviaciones estándar españolas: C/ para Calle, Avda. para Avenida, Pza. para Plaza, Ctra. para Carretera. Ejemplo: "C/ Virgen del Pilar, 34, 3ºA, 28820 Coslada, Madrid"
- Conceptos de trabajo: primera letra mayúscula, resto minúsculas, excepto nombres propios
- Materiales: primera letra mayúscula, marcas comerciales respetadas
- NIF/CIF: siempre en mayúsculas con formato correcto

Devuelve SOLO el JSON, sin texto adicional.
"""

    respuesta = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    contenido = respuesta.choices[0].message.content.strip()
    contenido = contenido.replace("```json", "").replace("```", "").strip()
    return json.loads(contenido)


async def handle_valor_campo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el texto del campo corregido, lo interpreta con GPT y actualiza"""
    campo = context.user_data.get("campo_editando")
    valor = update.message.text
    datos = context.user_data.get("datos_factura")

    await update.message.reply_text("⚙️ Interpretando...")

    try:
        resultado = await interpretar_campo_con_gpt(campo, valor, datos)

        for key, val in resultado.items():
            datos[key] = val

    except Exception:
        await update.message.reply_text(
            "❌ No he podido interpretar ese valor. Intenta escribirlo de otra forma."
        )
        return ESPERANDO_VALOR_CAMPO

    # Recalcula el total en Python siempre
    total_materiales = sum(m["precio"] for m in datos["materiales"]) if datos["materiales"] else 0
    total_horas = (datos["horas"] or 0) * (datos["precio_hora"] or 0)
    datos["total"] = round(total_materiales + total_horas + (datos["desplazamiento"] or 0), 2)

    context.user_data["datos_factura"] = datos
    context.user_data["campos_editados"].append(campo)
    context.user_data["numero_ediciones"] = context.user_data.get("numero_ediciones", 0) + 1

    tipo = datos.get("tipo", "factura")
    await update.message.reply_text(
        construir_resumen(datos),
        parse_mode="Markdown",
        reply_markup=construir_teclado_confirmacion(tipo)
    )
    return ESPERANDO_CONFIRMACION

async def handle_voice_campo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe un audio para editar un campo, transcribe y procesa igual que texto"""
    campo = context.user_data.get("campo_editando")
    datos = context.user_data.get("datos_factura")

    await update.message.reply_text("🎙️ Audio recibido, transcribiendo...")

    file = await context.bot.get_file(update.message.voice.file_id)
    audio_path = "audio_campo.ogg"
    await file.download_to_drive(audio_path)

    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="es"
        )

    valor = transcription.text
    await update.message.reply_text(
        f"📝 He entendido: _{valor}_",
        parse_mode="Markdown"
    )
    await update.message.reply_text("⚙️ Interpretando...")

    try:
        resultado = await interpretar_campo_con_gpt(campo, valor, datos)
        for key, val in resultado.items():
            datos[key] = val
    except Exception:
        await update.message.reply_text(
            "❌ No he podido interpretar el audio. "
            "Intenta de nuevo o escríbelo."
        )
        return ESPERANDO_VALOR_CAMPO

    # Recalcula el total en Python siempre
    total_materiales = sum(m["precio"] for m in datos["materiales"]) if datos["materiales"] else 0
    total_horas = (datos["horas"] or 0) * (datos["precio_hora"] or 0)
    datos["total"] = round(total_materiales + total_horas + (datos["desplazamiento"] or 0), 2)

    context.user_data["datos_factura"] = datos
    context.user_data["campos_editados"].append(campo)
    context.user_data["numero_ediciones"] = context.user_data.get("numero_ediciones", 0) + 1

    tipo = datos.get("tipo", "factura")
    await update.message.reply_text(
        construir_resumen(datos),
        parse_mode="Markdown",
        reply_markup=construir_teclado_confirmacion(tipo)
    )
    return ESPERANDO_CONFIRMACION


async def generar_y_enviar_pdf(query, context):
    """Genera el PDF y lo envía — llamado desde callback query"""
    datos = context.user_data.get("datos_factura")
    tipo = datos.get("tipo", "factura")
    es_presupuesto = tipo == "presupuesto"
    chat_id = query.message.chat_id
    config = cargar_config(chat_id)

    if context.user_data.get("usar_iban") and config.get("iban"):
        config["mostrar_iban"] = True
    else:
        config["mostrar_iban"] = False

    await query.message.reply_text(
        "⏳ Generando presupuesto PDF..." if es_presupuesto else "⏳ Generando factura PDF..."
    )
    numero = get_siguiente_numero_presupuesto(chat_id) if es_presupuesto else get_siguiente_numero_factura(chat_id)
    iva_porcentaje = config.get("iva", 0.21)
    nombre_pdf = generar_factura_pdf(datos, numero_factura=numero, info_autonomo=config, tipo=tipo, iva_porcentaje=iva_porcentaje)
    prefijo = "presupuesto" if es_presupuesto else "factura"
    emoji = "📋" if es_presupuesto else "🎉"
    segundos = int(datetime.now().timestamp() - context.user_data.get("tiempo_inicio", datetime.now().timestamp()))
    guardar_log(chat_id, {
        "transcripcion": context.user_data.get("transcripcion"),
        "tipo_detectado": context.user_data.get("tipo_detectado"),
        "json_estructurado": datos,
        "tipo_final": tipo,
        "campos_editados": context.user_data.get("campos_editados", []),
        "numero_ediciones": context.user_data.get("numero_ediciones", 0),
        "numero_documento": numero,
        "total_factura": datos.get("total"),
        "concepto": datos.get("concepto"),
        "accion_final": "confirmado",
        "segundos_hasta_confirmacion": segundos
    })
    with open(nombre_pdf, "rb") as pdf:
        await query.message.reply_document(
            document=pdf,
            filename=f"{prefijo}_{numero}.pdf",
            caption=f"{emoji} {prefijo.capitalize()} *{numero}* generada.\n"
                    f"Manda un audio cuando quieras crear la siguiente.",
            parse_mode="Markdown"
        )
    teclado_nuevo = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Nueva factura", callback_data="nueva_factura"),
            InlineKeyboardButton("📋 Nuevo presupuesto", callback_data="nueva_factura")
        ]
    ])
    await query.message.reply_text("¿Quieres crear otro documento?", reply_markup=teclado_nuevo)


async def generar_y_enviar_pdf_texto(update, context):
    """Genera el PDF y lo envía — llamado desde mensaje de texto"""
    datos = context.user_data.get("datos_factura")
    tipo = datos.get("tipo", "factura")
    es_presupuesto = tipo == "presupuesto"
    chat_id = update.effective_chat.id
    config = cargar_config(chat_id)
    if context.user_data.get("usar_iban") and config.get("iban"):
        config["mostrar_iban"] = True
    else:
        config["mostrar_iban"] = False
    await update.message.reply_text(
        "⏳ Generando presupuesto PDF..." if es_presupuesto else "⏳ Generando factura PDF..."
    )
    numero = get_siguiente_numero_presupuesto(chat_id) if es_presupuesto else get_siguiente_numero_factura(chat_id)
    iva_porcentaje = config.get("iva", 0.21)
    nombre_pdf = generar_factura_pdf(datos, numero_factura=numero, info_autonomo=config, tipo=tipo, iva_porcentaje=iva_porcentaje)
    prefijo = "presupuesto" if es_presupuesto else "factura"
    emoji = "📋" if es_presupuesto else "🎉"
    with open(nombre_pdf, "rb") as pdf:
        await update.message.reply_document(
            document=pdf,
            filename=f"{prefijo}_{numero}.pdf",
            caption=f"{emoji} {prefijo.capitalize()} *{numero}* generada.\n"
                    f"Manda un audio cuando quieras crear la siguiente.",
            parse_mode="Markdown"
        )
    teclado_nuevo = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Nueva factura", callback_data="nueva_factura"),
            InlineKeyboardButton("📋 Nuevo presupuesto", callback_data="nueva_factura")
        ]
    ])
    await update.message.reply_text("¿Quieres crear otro documento?", reply_markup=teclado_nuevo)


async def handle_iban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el IBAN, lo guarda y genera la factura"""
    iban = update.message.text.strip().upper()
    chat_id = update.effective_chat.id
    guardar_iban(chat_id, iban)
    context.user_data["usar_iban"] = True
    await update.message.reply_text("✅ IBAN guardado correctamente.")
    await generar_y_enviar_pdf_texto(update, context)
    return ESPERANDO_CONFIRMACION


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /cancelar — sale del flujo en cualquier momento"""
    await update.message.reply_text(
        "Operación cancelada. Envía /start para empezar de nuevo.",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data.clear()
    return ConversationHandler.END
async def privacidad(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔒 *Política de privacidad de FacturaVoz*\n\n"
        "• Procesamos tus notas de voz únicamente para generar facturas y presupuestos.\n"
        "• Si aceptaste el consentimiento completo, guardamos transcripciones para mejorar el servicio.\n"
        "• Nunca compartimos tus datos con terceros.\n"
        "• Puedes solicitar el borrado de tus datos escribiendo a blaigraw@gmail.com\n\n"
        "Para más información sobre el RGPD visita: https://www.aepd.es",
        parse_mode="Markdown"
    )
async def cmd_perfil(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Muestra el perfil del autónomo con opciones de edición"""
    chat_id = update.effective_chat.id
    config = cargar_config(chat_id)
    if not config:
        await update.message.reply_text("No tienes perfil configurado. Usa /start para registrarte.")
        return
    iban_texto = config.get("iban") or "No configurado"
    if config.get("iban") and len(config["iban"]) > 8:
        iban_texto = config["iban"][:8] + "..."
    iva_actual = config.get("iva", 0.21)
    iva_texto = "21% General" if iva_actual == 0.21 else "10% Reducido" if iva_actual == 0.10 else "Sin IVA"
    texto = (
        f"👤 *Tu perfil:*\n\n"
        f"*Nombre:* {config['nombre']}\n"
        f"*NIF:* {config['nif']}\n"
        f"*Dirección:* {config['direccion']}\n"
        f"*Teléfono:* {config['telefono']}\n"
        f"*Email:* {config['email']}\n"
        f"*IBAN:* {iban_texto}\n"
        f"*IVA habitual:* {iva_texto}\n\n"
        f"Pulsa un campo para editarlo:"
    )
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 Nombre", callback_data="perfil_nombre"),
            InlineKeyboardButton("🪪 NIF", callback_data="perfil_nif"),
        ],
        [
            InlineKeyboardButton("📍 Dirección", callback_data="perfil_direccion"),
            InlineKeyboardButton("📞 Teléfono", callback_data="perfil_telefono"),
        ],
        [
            InlineKeyboardButton("📧 Email", callback_data="perfil_email"),
            InlineKeyboardButton("💳 IBAN", callback_data="perfil_iban"),
        ],
        [
            InlineKeyboardButton("🧾 IVA", callback_data="perfil_iva"),
        ]
    ])
    await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado)


async def handle_perfil_callbacks(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Gestiona los callbacks del comando /perfil y edición de IVA"""
    query = update.callback_query
    await query.answer()

    if query.data == "perfil_iva":
        teclado_iva = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("21% General", callback_data="setiva_21"),
                InlineKeyboardButton("10% Reducido", callback_data="setiva_10"),
                InlineKeyboardButton("Sin IVA", callback_data="setiva_0")
            ]
        ])
        await query.message.reply_text(
            "🧾 Selecciona tu IVA habitual:",
            reply_markup=teclado_iva
        )

    elif query.data in ("setiva_21", "setiva_10", "setiva_0"):
        porcentaje = {"setiva_21": 0.21, "setiva_10": 0.10, "setiva_0": 0.0}[query.data]
        etiqueta = {"setiva_21": "21% General", "setiva_10": "10% Reducido", "setiva_0": "Sin IVA"}[query.data]
        guardar_iva(query.message.chat_id, porcentaje)
        await query.message.reply_text(f"✅ IVA actualizado a {etiqueta}.")


async def post_init(application):
    await application.bot.set_my_commands([
        ("start", "Iniciar o reiniciar el bot"),
        ("cancelar", "Cancelar la operación actual"),
        ("perfil", "Ver y editar tus datos"),
        ("privacidad", "Política de privacidad"),
    ])


# ConversationHandler — gestiona el estado de cada usuario
conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler("start", start),
        MessageHandler(filters.VOICE, handle_voice)
    ],
    states={
        REGISTRO_NOMBRE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, registro_nombre)
        ],
        REGISTRO_NIF: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, registro_nif)
        ],
        REGISTRO_DIRECCION: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, registro_direccion)
        ],
        REGISTRO_TELEFONO: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, registro_telefono)
        ],
        REGISTRO_EMAIL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, registro_email)
        ],
        ESPERANDO_AUDIO: [
            MessageHandler(filters.VOICE, handle_voice),
            CallbackQueryHandler(handle_perfil_callbacks, pattern="^(perfil_|setiva_)"),
            CallbackQueryHandler(handle_confirmacion)
        ],
        ESPERANDO_CONFIRMACION: [
            CallbackQueryHandler(handle_perfil_callbacks, pattern="^(perfil_|setiva_)"),
            CallbackQueryHandler(handle_confirmacion),
        ],
        ESPERANDO_VALOR_CAMPO: [
            MessageHandler(filters.VOICE, handle_voice_campo),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_valor_campo),
            CallbackQueryHandler(handle_perfil_callbacks, pattern="^(perfil_|setiva_)"),
        ],
        ESPERANDO_CONSENTIMIENTO: [
            CallbackQueryHandler(handle_consentimiento)
        ],
        ESPERANDO_IBAN: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_iban),
        ],
    },
    fallbacks=[
        CommandHandler("cancelar", cancelar),
        CommandHandler("start", start),
        CallbackQueryHandler(handle_perfil_callbacks, pattern="^(perfil_|setiva_)"),
        CallbackQueryHandler(handle_consentimiento, pattern="^consent_")
    ]
)

# Construye y arranca el bot
app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
app.add_handler(conv_handler)

init_db()
print("Bot activo...")
app.add_handler(CommandHandler("privacidad", privacidad))
app.add_handler(CommandHandler("perfil", cmd_perfil))
app.add_handler(CallbackQueryHandler(handle_perfil_callbacks, pattern="^(perfil_|setiva_)"))
app.run_polling()
