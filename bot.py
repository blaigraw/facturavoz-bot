import os
import json
from datetime import datetime
from config import config_existe, guardar_config, cargar_config, get_siguiente_numero_factura, get_siguiente_numero_presupuesto, init_db, guardar_log, guardar_consentimiento, tiene_consentimiento, guardar_iban, guardar_iva, eliminar_usuario, eliminar_logs, get_pruebas_realizadas, incrementar_prueba
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
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

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
EDITANDO_PERFIL_CAMPO = 11
CONFIRMANDO_PERFIL_CAMPO = 12
REGISTRO_ACTIVIDAD = 13
ONBOARDING_PRUEBA = 14
ONBOARDING_REGISTRO = 15

def get_prompt_sistema():
    hoy = datetime.now().strftime("%d/%m/%Y")
    return f"""Eres un asistente de facturación para autónomos de construcción en España.
Extrae datos de una nota de voz informal y devuelve SOLO este JSON, sin texto adicional ni bloques de código:

{{"tipo": null, "cliente_nombre": null, "cliente_direccion": null, "concepto": null,
"observaciones": null, "materiales": [{{"descripcion": null, "precio": null}}],
"horas": null, "precio_hora": null, "desplazamiento": null,
"validez_dias": null, "fecha": "{hoy}", "total": 0.00}}

REGLAS:
- Datos no mencionados → null
- tipo: "factura" si menciona cobrar/trabajo terminado, "presupuesto" si menciona precio aproximado/cuánto costaría, null si no está claro
- validez_dias: el que indique el audio, 30 por defecto si es presupuesto, null si es factura
- fecha: usa {hoy} por defecto. Fechas relativas ("ayer", "el lunes"...) calcúlalas desde {hoy} en DD/MM/YYYY
- cliente_nombre: capitaliza correctamente. Empresas en su formato oficial
- cliente_direccion: formato postal español con abreviaciones (C/, Avda., Pza., Ctra.). Ejemplo: "C/ Mayor, 4, 2ºB, 28013 Madrid". CP solo si estás seguro
- concepto: primera letra mayúscula, resto minúsculas. Redacta de forma clara y profesional
- materiales descripcion: primera letra mayúscula, resto minúsculas. Marcas comerciales respetadas (Roca, Grohe, Schneider, Legrand, Baxi, Ferroli...)
- materiales precio — cliente vs coste: si el autónomo menciona dos precios para un mismo material, uno como lo que le costó y otro como lo que cobra al cliente, usa SIEMPRE el precio que cobra al cliente. Ejemplos: "me costó 108 pero cobro 135" → 135 / "lo compré por 40, al cliente le pongo 50" → 50
- materiales sin precio: si el autónomo dice que no sabe el precio, no lo recuerda, lo tiene que confirmar, o usa expresiones como "no sé cuánto fue", "no me acuerdo", "lo miro luego", "tengo que confirmarlo" → usa "precio": null. NUNCA uses 0.0 cuando el precio no se menciona o se indica incertidumbre.
- materiales con precio individual: si cada material tiene su propio precio mencionado explícitamente → crea un ítem separado por material.
- materiales agrupados: SOLO agrupa materiales en un único ítem cuando el autónomo mencione explícitamente un precio total compartido entre varios con expresiones como "todo junto", "entre los dos", "en total", "todo eso junto". Ejemplo correcto: "el adhesivo y las crucetas, todo junto 8 euros" → {{"descripcion": "Adhesivo y crucetas", "precio": 8.0}}. Ejemplo incorrecto: agrupar materiales con precios distintos o cuando uno no tiene precio.
- observaciones: solo si menciona explícitamente algo para anotar (pago en efectivo, garantía, certificado). Máximo 2 líneas. Si no → null
- total: suma materiales + (horas × precio_hora) + desplazamiento"""

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

    if datos.get("observaciones"):
        resumen += f"📝 *Observaciones:* {datos['observaciones']}\n"

    if datos["materiales"]:
        resumen += "\n📦 *Materiales:*\n"
        for m in datos["materiales"]:
            precio_texto = f"{m['precio']}€" if m.get("precio") is not None else "Pendiente"
            resumen += f"  • {m['descripcion']}: {precio_texto}\n"

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
        f"💵 *TOTAL: {round((datos['total'] or 0) * (1 + iva_porcentaje), 2)}€*\n"
    )
    resumen += "\n_✏️ Pulsa *Editar campo* si necesitas corregir algo._\n\n¿Es correcto?"
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
        ],
        [
            InlineKeyboardButton("📝 Observaciones", callback_data="campo_observaciones"),
        ],
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
    context.user_data.clear()
    chat_id = update.effective_chat.id


    # Usuario ya registrado — flujo normal
    if config_existe(chat_id):
        config = cargar_config(chat_id)
        texto = (
            f"👋 Hola de nuevo, {config['nombre']}.\n\n"
            "Envíame una nota de voz describiendo el trabajo."
        )
        await update.message.reply_text(texto, parse_mode="Markdown")
        return ESPERANDO_AUDIO

    # Usuario nuevo — onboarding
    pruebas = get_pruebas_realizadas(chat_id)

    if pruebas >= 3:
        teclado = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Configurar mi perfil", callback_data="onboarding_registrar")
        ]])
        await update.message.reply_text(
            "Ya has visto cómo funciona FacturaVoz 😊\n\n"
            "Para seguir creando facturas con tus datos reales "
            "necesitas configurar tu perfil.\n"
            "Son 2 minutos — solo una vez.",
            parse_mode="Markdown",
            reply_markup=teclado
        )
        return ONBOARDING_REGISTRO

    # Primera vez o tiene pruebas disponibles
    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🎙️ Hacer mi prueba gratis", callback_data="onboarding_prueba")
    ]])
    await update.message.reply_text(
        "👋 Bienvenido a *FacturaVoz*\n\n"
        "Tu asistente personal para facturas y presupuestos.\n"
        "Sin formularios. Sin Excel. Sin perder el tiempo.\n\n"
        "Graba un audio describiendo un trabajo y en segundos "
        "tienes el PDF listo para enviar. 📄\n\n"
        "Hazte una prueba gratis — sin dar ningún dato todavía. 👇",
        parse_mode="Markdown",
        reply_markup=teclado
    )
    return ONBOARDING_PRUEBA

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
    """Recibe el email y pregunta la actividad"""
    teclado_actividad = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔧 Fontanero", callback_data="act_fontanero"),
            InlineKeyboardButton("⚡ Electricista", callback_data="act_electricista"),
        ],
        [
            InlineKeyboardButton("🏗️ Reformas", callback_data="act_reformas"),
            InlineKeyboardButton("🎨 Pintor", callback_data="act_pintor"),
        ],
        [
            InlineKeyboardButton("🪚 Carpintero", callback_data="act_carpintero"),
            InlineKeyboardButton("❓ Otro", callback_data="act_otro"),
        ]
    ])
    context.user_data["reg_email"] = update.message.text
    await update.message.reply_text(
        "¿Cuál es tu actividad principal?",
        reply_markup=teclado_actividad
    )
    return REGISTRO_ACTIVIDAD


async def handle_actividad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe la actividad del autónomo y completa el registro"""
    query = update.callback_query
    await query.answer()
    actividades = {
        "act_fontanero": "Fontanero",
        "act_electricista": "Electricista",
        "act_reformas": "Reformas",
        "act_pintor": "Pintor",
        "act_carpintero": "Carpintero",
        "act_otro": "Otro"
    }
    actividad = actividades.get(query.data, "Otro")
    config = {
        "nombre": context.user_data["reg_nombre"],
        "nif": context.user_data["reg_nif"],
        "direccion": context.user_data["reg_direccion"],
        "telefono": context.user_data["reg_telefono"],
        "email": context.user_data["reg_email"],
        "actividad": actividad
    }
    chat_id = query.message.chat_id
    guardar_config(chat_id, config)
    guardar_iva(chat_id, 0.21)
    await query.edit_message_text(
        f"✅ *Registro completado.*\n\n"
        f"👤 *Nombre:* {config['nombre']}\n"
        f"🪪 *NIF:* {config['nif']}\n"
        f"📍 *Dirección:* {config['direccion']}\n"
        f"📞 *Teléfono:* {config['telefono']}\n"
        f"📧 *Email:* {config['email']}\n"
        f"🔧 *Actividad:* {actividad}\n\n"
        f"💡 Tu IVA habitual está configurado al 21%. "
        f"Puedes cambiarlo en /perfil.\n\n"
        f"Ya puedes empezar. Envíame una nota de voz "
        f"describiendo el trabajo.",
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
        total_materiales = sum(m["precio"] for m in datos["materiales"] if m.get("precio") is not None) if datos["materiales"] else 0
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
        materiales_sin_precio = [
            m for m in (datos.get("materiales") or [])
            if m.get("precio") is None
        ]

        if materiales_sin_precio:
            nombres = ", ".join(m["descripcion"] for m in materiales_sin_precio)
            teclado_pendiente = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Completar ahora", callback_data="editar")],
                [InlineKeyboardButton("📋 Marcar como pendiente", callback_data="mat_pendiente")],
                [InlineKeyboardButton("➡️ Dejar en blanco", callback_data="mat_blanco")]
            ])
            await query.message.reply_text(
                f"⚠️ Estos materiales no tienen precio:\n_{nombres}_\n\n"
                f"¿Cómo quieres gestionarlos?",
                parse_mode="Markdown",
                reply_markup=teclado_pendiente
            )
            return ESPERANDO_CONFIRMACION

        tipo = datos.get("tipo", "factura")

        if tipo == "factura" and not context.user_data.get("modo_prueba"):
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
                f"{'IBAN guardado: ' + config['iban'][:8] + '...' if config and config.get('iban') else 'No tienes IBAN configurado.'}",
                reply_markup=teclado_iban
            )
            return ESPERANDO_CONFIRMACION

        return await generar_y_enviar_pdf(query, context)

    elif query.data == "iban_no":
        context.user_data["usar_iban"] = False
        return await generar_y_enviar_pdf(query, context)

    elif query.data == "iban_si":
        config = cargar_config(query.message.chat_id)
        if config and config.get("iban"):
            context.user_data["usar_iban"] = True
            return await generar_y_enviar_pdf(query, context)
        else:
            await query.message.reply_text(
                "💳 Escribe tu IBAN:\n"
                "Ejemplo: ES91 2100 0418 4200 0512 3456"
            )
            return ESPERANDO_IBAN

    elif query.data == "audio_reemplazar":
        audio_path = context.user_data.get("audio_pendiente_path")
        if not audio_path:
            await query.message.reply_text("❌ No encontré el audio.")
            return ESPERANDO_CONFIRMACION
        context.user_data.clear()
        await query.message.reply_text("🎙️ Procesando audio...")
        with open(audio_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="es"
            )
        texto = transcription.text
        await query.message.reply_text(
            f"📝 He entendido:\n_{texto}_",
            parse_mode="Markdown"
        )
        await query.message.reply_text("⚙️ Estructurando datos...")
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
            total_materiales = sum(m["precio"] for m in datos["materiales"] if m.get("precio") is not None) if datos.get("materiales") else 0
            total_horas = (datos.get("horas") or 0) * (datos.get("precio_hora") or 0)
            datos["total"] = round(total_materiales + total_horas + (datos.get("desplazamiento") or 0), 2)
            context.user_data["datos_factura"] = datos
            context.user_data["transcripcion"] = texto
            context.user_data["tipo_detectado"] = datos.get("tipo")
            context.user_data["campos_editados"] = []
            context.user_data["numero_ediciones"] = 0
            context.user_data["tiempo_inicio"] = datetime.now().timestamp()
            tipo = datos.get("tipo")
            if not tipo:
                teclado_tipo = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📄 Factura", callback_data="tipo_factura"),
                        InlineKeyboardButton("📋 Presupuesto", callback_data="tipo_presupuesto")
                    ]
                ])
                await query.message.reply_text(
                    "🤖 No he entendido si es una factura o un presupuesto.\n"
                    "¿Qué documento necesitas?",
                    reply_markup=teclado_tipo
                )
            else:
                config = cargar_config(query.message.chat_id)
                iva_porcentaje = config.get("iva", 0.21) if config else 0.21
                await query.message.reply_text(
                    construir_resumen(datos, iva_porcentaje),
                    parse_mode="Markdown",
                    reply_markup=construir_teclado_confirmacion(tipo)
                )
        except json.JSONDecodeError:
            await query.message.reply_text(
                "❌ No he podido estructurar los datos. "
                "Envía otra nota de voz."
            )
        return ESPERANDO_CONFIRMACION

    elif query.data == "audio_cancelar":
        await query.edit_message_text(
            "↩️ Volvemos a lo que tenías."
        )
        datos = context.user_data.get("datos_factura")
        if datos:
            config = cargar_config(query.message.chat_id)
            iva_porcentaje = config.get("iva", 0.21) if config else 0.21
            await query.message.reply_text(
                construir_resumen(datos, iva_porcentaje),
                parse_mode="Markdown",
                reply_markup=construir_teclado_confirmacion(datos.get("tipo", "factura"))
            )
        return ESPERANDO_CONFIRMACION

    elif query.data == "mat_pendiente":
        datos = context.user_data.get("datos_factura")
        for m in datos.get("materiales", []):
            if m.get("precio") is None:
                m["precio"] = "Pendiente"
        context.user_data["datos_factura"] = datos
        return await generar_y_enviar_pdf(query, context)

    elif query.data == "mat_blanco":
        datos = context.user_data.get("datos_factura")
        for m in datos.get("materiales", []):
            if m.get("precio") is None:
                m["precio"] = ""
        context.user_data["datos_factura"] = datos
        return await generar_y_enviar_pdf(query, context)

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
            "total": "total en €",
            "observaciones": "observaciones",
        }
        if campo == "observaciones":
            texto_peticion = (
                "📝 *Observaciones*: envía un audio o escríbelo.\n"
                "Para eliminarlas escribe *eliminar*.\n"
                "/cancelar para salir."
            )
        else:
            texto_peticion = (
                f"✏️ *{nombres[campo].capitalize()}*: envía un audio "
                f"o escríbelo directamente.\n/cancelar para salir."
            )
        await query.message.reply_text(
            texto_peticion,
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
- Direcciones en formato postal español: "Calle/Avenida/Plaza Nombre, número, piso, CP Municipio, Provincia". Si el usuario menciona explícitamente un código postal, inclúyelo siempre. Si no lo menciona, solo ponlo si estás seguro al 100%. Usa abreviaciones estándar: C/ para Calle, Avda. para Avenida, Pza. para Plaza, Ctra. para Carretera. Ejemplo: "C/ Virgen del Pilar, 34, 3ºA, 28820 Coslada, Madrid"
- Conceptos de trabajo: primera letra mayúscula, resto minúsculas, excepto nombres propios
- Materiales: primera letra mayúscula, marcas comerciales respetadas
- NIF/NIE: siempre en mayúsculas, sin espacios ni guiones. DNI: 8 dígitos + letra (ej: "12345678A"). NIE: letra + 7 dígitos + letra (ej: "X1234567B")
- Teléfono: formato español con espacios cada 3 dígitos, ej: "634 812 755"
- IBAN: elimina espacios, convierte a mayúsculas, luego formatea en grupos de 4. Ejemplo: {{"iban": "ES91 2100 0418 4200 0512 3456"}}
- observaciones: {{"observaciones": "Cliente pagó en efectivo."}} ← texto limpio, máximo 2 líneas
  Si el usuario dice "eliminar", "quitar", "borrar" o similar → {{"observaciones": null}}

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

    if campo == "observaciones" and valor.lower().strip() in ["eliminar", "quitar", "borrar", "borra", "quita", "elimina"]:
        datos["observaciones"] = None
        context.user_data["datos_factura"] = datos
        tipo = datos.get("tipo", "factura")
        await update.message.reply_text(
            construir_resumen(datos),
            parse_mode="Markdown",
            reply_markup=construir_teclado_confirmacion(tipo)
        )
        return ESPERANDO_CONFIRMACION

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
    total_materiales = sum(m["precio"] for m in datos["materiales"] if m.get("precio") is not None) if datos["materiales"] else 0
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
    total_materiales = sum(m["precio"] for m in datos["materiales"] if m.get("precio") is not None) if datos["materiales"] else 0
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
    modo_prueba = context.user_data.get("modo_prueba", False)
    if modo_prueba:
        incrementar_prueba(chat_id)
    config = cargar_config(chat_id)

    if config:
        if context.user_data.get("usar_iban") and config.get("iban"):
            config["mostrar_iban"] = True
        else:
            config["mostrar_iban"] = False

    await query.message.reply_text(
        "⏳ Generando presupuesto PDF..." if es_presupuesto else "⏳ Generando factura PDF..."
    )
    numero = get_siguiente_numero_presupuesto(chat_id) if es_presupuesto else get_siguiente_numero_factura(chat_id)
    nombre_pdf = generar_factura_pdf(
        datos,
        numero_factura=numero,
        info_autonomo=cargar_config(chat_id) if not modo_prueba else None,
        tipo=tipo,
        es_prueba=modo_prueba
    )
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
    if modo_prueba:
        pruebas_hechas = get_pruebas_realizadas(chat_id)
        pruebas_restantes = 3 - pruebas_hechas
        if pruebas_restantes > 0:
            texto_pruebas = f"Te {'queda' if pruebas_restantes == 1 else 'quedan'} {pruebas_restantes} {'prueba' if pruebas_restantes == 1 else 'pruebas'} gratis."
        else:
            texto_pruebas = "Has usado todas tus pruebas gratis."

        botones = [[InlineKeyboardButton(
            "✅ Configurar mi perfil", callback_data="onboarding_registrar")]]
        if pruebas_restantes > 0:
            botones.append([InlineKeyboardButton(
                "🔄 Hacer otra prueba", callback_data="onboarding_prueba")])

        teclado_post_prueba = InlineKeyboardMarkup(botones)
        await query.message.reply_text(
            f"¿Ves qué fácil? 🎉\n\n"
            f"Esto es lo que FacturaVoz hace cada vez.\n"
            f"Para que la factura salga con tu nombre y NIF,\n"
            f"configura tu perfil — solo una vez, 2 minutos.\n\n"
            f"{texto_pruebas}",
            parse_mode="Markdown",
            reply_markup=teclado_post_prueba
        )
        print(f"DEBUG estado post-prueba — devolviendo ONBOARDING_REGISTRO")
        return ONBOARDING_REGISTRO
    teclado_nuevo = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Nueva factura", callback_data="nueva_factura"),
            InlineKeyboardButton("📋 Nuevo presupuesto", callback_data="nueva_factura")
        ]
    ])
    await query.message.reply_text("¿Quieres crear otro documento?", reply_markup=teclado_nuevo)
    return ESPERANDO_CONFIRMACION


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


async def handle_voice_inesperado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestiona un audio mandado mientras hay un resumen activo"""
    file = await context.bot.get_file(update.message.voice.file_id)
    audio_path = "audio_pendiente.ogg"
    await file.download_to_drive(audio_path)
    context.user_data["audio_pendiente_path"] = audio_path

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Empezar nuevo", callback_data="audio_reemplazar"),
            InlineKeyboardButton("↩️ Volver al actual", callback_data="audio_cancelar")
        ]
    ])
    await update.message.reply_text(
        "⚠️ Ya tienes un documento en curso.\n\n"
        "¿Qué quieres hacer con este audio?",
        reply_markup=teclado
    )
    return ESPERANDO_CONFIRMACION


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /cancelar — sale del flujo en cualquier momento"""
    await update.message.reply_text(
        "Operación cancelada. Envía /start para empezar de nuevo.",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data.clear()
    return ConversationHandler.END
async def admin_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina el registro de un usuario — solo para el admin"""
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return  # Silencioso — no revela que existe el comando

    args = context.args
    if not args:
        target_id = update.effective_chat.id
    else:
        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("❌ ID inválido.")
            return

    eliminar_logs(target_id)
    eliminar_usuario(target_id)

    await update.message.reply_text(
        f"✅ Usuario {target_id} eliminado de la base de datos."
    )


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
    """Muestra el perfil del autónomo"""
    chat_id = update.effective_chat.id
    config = cargar_config(chat_id)
    if not config:
        await update.message.reply_text(
            "No tienes perfil configurado. Usa /start para registrarte."
        )
        return
    await mostrar_perfil(update.message, chat_id)


async def mostrar_perfil(message, chat_id):
    """Muestra el perfil completo del autónomo"""
    config = cargar_config(chat_id)
    iban_texto = "No configurado"
    if config.get("iban"):
        iban_raw = config["iban"].replace(" ", "").upper()
        iban_texto = " ".join(iban_raw[i:i+4] for i in range(0, len(iban_raw), 4))
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
    await message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado)


async def handle_perfil_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestiona los callbacks del comando /perfil y edición de campos"""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    # IVA — flujo directo sin confirmación
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
        return

    elif query.data in ("setiva_21", "setiva_10", "setiva_0"):
        porcentaje = {"setiva_21": 0.21, "setiva_10": 0.10, "setiva_0": 0.0}[query.data]
        guardar_iva(chat_id, porcentaje)
        await mostrar_perfil(query.message, chat_id)
        return

    # Campos de perfil — pide el nuevo valor
    campos_perfil = {
        "perfil_nombre": ("nombre", "👤 Nombre actual: {valor}\n\nEnvía el nuevo nombre en audio o escrito."),
        "perfil_nif": ("nif", "🪪 NIF actual: {valor}\n\nEnvía el nuevo NIF en audio o escrito."),
        "perfil_direccion": ("direccion", "📍 Dirección actual: {valor}\n\nEnvía la nueva dirección en audio o escrito."),
        "perfil_telefono": ("telefono", "📞 Teléfono actual: {valor}\n\nEnvía el nuevo teléfono en audio o escrito."),
        "perfil_email": ("email", "📧 Email actual: {valor}\n\nEnvía el nuevo email en audio o escrito."),
        "perfil_iban": ("iban", "💳 IBAN actual: {valor}\n\nEnvía el nuevo IBAN en audio o escrito."),
    }

    if query.data in campos_perfil:
        campo, mensaje_template = campos_perfil[query.data]
        config = cargar_config(chat_id)
        valor_actual = config.get(campo) or "No configurado"
        context.user_data["perfil_campo_editando"] = campo
        await query.message.reply_text(
            mensaje_template.format(valor=valor_actual) + "\n\n/cancelar para salir."
        )
        return EDITANDO_PERFIL_CAMPO

    elif query.data == "perfil_confirmar":
        campo = context.user_data.get("perfil_campo_editando")
        nuevo_valor = context.user_data.get("perfil_valor_pendiente")
        config = cargar_config(chat_id)
        config[campo] = nuevo_valor
        guardar_config(chat_id, config)
        await query.message.reply_text("✅ Guardado correctamente.")
        await mostrar_perfil(query.message, chat_id)
        return ESPERANDO_AUDIO

    elif query.data == "perfil_repetir":
        campo = context.user_data.get("perfil_campo_editando")
        campos_nombres = {
            "nombre": "nombre", "nif": "NIF", "direccion": "dirección",
            "telefono": "teléfono", "email": "email", "iban": "IBAN"
        }
        await query.message.reply_text(
            f"Envía el nuevo valor para {campos_nombres.get(campo, campo)} "
            f"en audio o escrito.\n\n/cancelar para salir."
        )
        return EDITANDO_PERFIL_CAMPO


async def handle_perfil_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe texto para actualizar un campo del perfil"""
    campo = context.user_data.get("perfil_campo_editando")
    if not campo:
        return
    valor_raw = update.message.text.strip()
    await update.message.reply_text("⚙️ Interpretando...")
    try:
        resultado = await interpretar_campo_con_gpt(campo, valor_raw, {})
        valor = list(resultado.values())[0]
        if not isinstance(valor, str):
            valor = str(valor)
    except Exception:
        valor = valor_raw
    context.user_data["perfil_valor_pendiente"] = valor
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar", callback_data="perfil_confirmar"),
            InlineKeyboardButton("🔄 Repetir", callback_data="perfil_repetir")
        ]
    ])
    await update.message.reply_text(
        f"¿Es correcto?\n\n*{valor}*",
        parse_mode="Markdown",
        reply_markup=teclado
    )


async def handle_perfil_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe audio para actualizar un campo del perfil"""
    campo = context.user_data.get("perfil_campo_editando")
    if not campo:
        return
    await update.message.reply_text("🎙️ Audio recibido, transcribiendo...")
    file = await context.bot.get_file(update.message.voice.file_id)
    audio_path = "audio_perfil.ogg"
    await file.download_to_drive(audio_path)
    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="es"
        )
    valor_raw = transcription.text
    await update.message.reply_text("⚙️ Interpretando...")
    try:
        resultado = await interpretar_campo_con_gpt(campo, valor_raw, {})
        valor = list(resultado.values())[0]
        if not isinstance(valor, str):
            valor = str(valor)
    except Exception:
        valor = valor_raw
    context.user_data["perfil_valor_pendiente"] = valor
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar", callback_data="perfil_confirmar"),
            InlineKeyboardButton("🔄 Repetir", callback_data="perfil_repetir")
        ]
    ])
    await update.message.reply_text(
        f"¿Es correcto?\n\n*{valor}*",
        parse_mode="Markdown",
        reply_markup=teclado
    )


async def handle_onboarding_prueba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    print(f"DEBUG onboarding_prueba llamado — query.data: {query.data}")
    await query.answer()
    await query.edit_message_text(
        "🎙️ *Graba tu audio ahora*\n\n"
        "Habla como si le explicaras el trabajo a un colega. "
        "Por ejemplo:\n\n"
        "_'Factura para Juan García, calle Mayor 5 Madrid, "
        "he cambiado la caldera, materiales 320 euros, "
        "4 horas a 35 euros, desplazamiento 20 euros'_\n\n"
        "Incluye si puedes:\n"
        "• Nombre y dirección del cliente\n"
        "• Trabajo realizado\n"
        "• Materiales y su precio\n"
        "• Horas y precio por hora\n"
        "• Desplazamiento\n\n"
        "Si algo no sale bien no pasa nada — "
        "podrás editar cualquier campo antes de confirmar. 👇",
        parse_mode="Markdown"
    )
    context.user_data["modo_prueba"] = True
    return ESPERANDO_AUDIO


async def handle_onboarding_registro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "¿Cuál es tu nombre completo o razón social?"
    )
    return REGISTRO_NOMBRE


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
        MessageHandler(filters.VOICE, handle_voice),
        CallbackQueryHandler(handle_onboarding_prueba, pattern="^onboarding_prueba$"),
        CallbackQueryHandler(handle_onboarding_registro, pattern="^onboarding_registrar$"),
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
        REGISTRO_ACTIVIDAD: [
            CallbackQueryHandler(handle_actividad, pattern="^act_")
        ],
        ONBOARDING_PRUEBA: [
            CallbackQueryHandler(handle_onboarding_prueba, pattern="^onboarding_prueba$"),
            CallbackQueryHandler(handle_onboarding_registro, pattern="^onboarding_registrar$"),
        ],
        ONBOARDING_REGISTRO: [
            CallbackQueryHandler(handle_onboarding_registro, pattern="^onboarding_registrar$"),
            CallbackQueryHandler(handle_onboarding_prueba, pattern="^onboarding_prueba$"),
        ],
        ESPERANDO_AUDIO: [
            MessageHandler(filters.VOICE, handle_voice),
            CallbackQueryHandler(handle_perfil_callbacks, pattern="^(perfil_|setiva_)"),
            CallbackQueryHandler(handle_confirmacion)
        ],
        ESPERANDO_CONFIRMACION: [
            MessageHandler(filters.VOICE, handle_voice_inesperado),
            CallbackQueryHandler(handle_perfil_callbacks, pattern="^(perfil_|setiva_)"),
            CallbackQueryHandler(handle_confirmacion),
            CallbackQueryHandler(handle_onboarding_prueba, pattern="^onboarding_prueba$"),
            CallbackQueryHandler(handle_onboarding_registro, pattern="^onboarding_registrar$"),
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
        EDITANDO_PERFIL_CAMPO: [
            MessageHandler(filters.VOICE, handle_perfil_audio),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_perfil_texto),
            CallbackQueryHandler(handle_perfil_callbacks, pattern="^(perfil_|setiva_)"),
        ],
    },
    fallbacks=[
        CommandHandler("cancelar", cancelar),
        CommandHandler("start", start),
        CallbackQueryHandler(handle_perfil_callbacks, pattern="^(perfil_|setiva_)"),
        CallbackQueryHandler(handle_consentimiento, pattern="^consent_")
    ]
)

import asyncio
from aiohttp import web

async def handle_webhook(request):
    """Recibe updates de Telegram vía webhook"""
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(status=200)

async def main():
    """Arranca el bot en modo webhook"""
    global app
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # Registra todos los handlers
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("privacidad", privacidad))
    app.add_handler(CommandHandler("perfil", cmd_perfil))
    app.add_handler(CommandHandler("admin_reset", admin_reset))
    app.add_handler(CallbackQueryHandler(
        handle_perfil_callbacks, pattern="^(perfil_|setiva_)"
    ))

    init_db()

    # Configura el webhook
    webhook_url = os.getenv("WEBHOOK_URL")
    port = int(os.getenv("PORT", 8080))

    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.bot.set_webhook(
        url=f"{webhook_url}/webhook",
        allowed_updates=["message", "callback_query"]
    )

    # Servidor web para recibir los updates
    web_app = web.Application()
    web_app.router.add_post("/webhook", handle_webhook)
    web_app.router.add_get("/health", lambda r: web.Response(text="OK"))

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)

    print(f"Bot activo en webhook — {webhook_url}/webhook")

    async with app:
        await app.start()
        await site.start()
        await asyncio.Event().wait()  # mantiene el proceso vivo

if __name__ == "__main__":
    asyncio.run(main())
