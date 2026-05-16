import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT

def _calcular_ajuste(subtotal: float, datos: dict, iva_rate: float) -> dict | None:
    """Lógica de ajuste independiente de la plantilla PDF — misma aritmética que bot.py."""
    precio_final = datos.get("precio_final")
    if precio_final is None:
        return None
    precio_final = float(precio_final)
    if iva_rate > 0:
        base = round(precio_final / (1 + iva_rate), 2)
        cuota = round(precio_final - base, 2)
        return {
            "ajuste": round(base - subtotal, 2),
            "base_imponible": base,
            "cuota_iva": cuota,
            "total": precio_final,
        }
    else:
        return {
            "ajuste": round(precio_final - subtotal, 2),
            "base_imponible": None,
            "cuota_iva": None,
            "total": precio_final,
        }


def generar_factura_pdf(datos, numero_factura=None, info_autonomo=None, tipo="factura",
                        iva_porcentaje=0.21, es_prueba=False):

    if es_prueba or not info_autonomo:
        info_autonomo = {
            "nombre": "Antonio Martínez García",
            "nif": "47382910B",
            "direccion": "C/ de la Construcción, 14, 2ºA, 28045 Madrid",
            "telefono": "612 345 678",
            "email": "antonio.martinez@gmail.com",
            "iban": None,
            "mostrar_iban": False
        }
        numero_factura = "PRUEBA"

    # Nombre del archivo — incluye fecha y cliente para fácil identificación
    fecha_hoy = datetime.now().strftime("%Y%m%d")
    import re
    cliente_slug = re.sub(r'[^a-zA-Z0-9]', '_', datos.get("cliente_nombre") or "cliente")[:20]
    prefijo_archivo = "presupuesto" if tipo == "presupuesto" else "factura"
    # Crea la carpeta si no existe
    os.makedirs("documentos", exist_ok=True)
    nombre_archivo = f"documentos/{prefijo_archivo}_{fecha_hoy}_{cliente_slug}.pdf"
    if es_prueba:
        nombre_archivo = f"documentos/prueba_{fecha_hoy}_{cliente_slug}.pdf"

    # Número de factura por defecto
    if not numero_factura:
        numero_factura = f"{datetime.now().strftime('%Y')}-{datetime.now().strftime('%m%d%H%M')}"

    # Crea el documento PDF
    doc = SimpleDocTemplate(
        nombre_archivo,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm
    )

    # Estilos de texto
    styles = getSampleStyleSheet()
    
    estilo_titulo = ParagraphStyle(
        "titulo",
        parent=styles["Normal"],
        fontSize=24,
        textColor=colors.HexColor("#2C3E50"),
        spaceAfter=0.3*cm
    )
    
    estilo_subtitulo = ParagraphStyle(
        "subtitulo",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#7F8C8D"),
    )

    estilo_normal = ParagraphStyle(
        "normal_custom",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#2C3E50"),
    )

    estilo_negrita = ParagraphStyle(
        "negrita",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#2C3E50"),
    )

    estilo_derecha = ParagraphStyle(
        "derecha",
        parent=styles["Normal"],
        fontSize=10,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#2C3E50"),
    )

    estilo_total = ParagraphStyle(
        "total",
        parent=styles["Normal"],
        fontSize=14,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#FFFFFF"),
        alignment=TA_RIGHT
    )

    # Contenido del PDF — lista de elementos
    elementos = []

    # ── CABECERA ──────────────────────────────────────────
    # Tabla con dos columnas: datos del autónomo | título FACTURA
    # ── CABECERA ──────────────────────────────────────────
    # ── CABECERA ──────────────────────────────────────────
    # Columna izquierda — datos del autónomo con espaciado entre campos
    col_izquierda = [
        Paragraph(info_autonomo.get("nombre") or "—", estilo_titulo),
        Spacer(1, 0.3*cm),
        Paragraph(f"NIF: {info_autonomo.get('nif') or '—'}", estilo_subtitulo),
        Spacer(1, 0.15*cm),
        Paragraph(info_autonomo.get("direccion") or "—", estilo_subtitulo),
        Spacer(1, 0.15*cm),
        Paragraph(f"Tel: {info_autonomo.get('telefono') or '—'}", estilo_subtitulo),
        Spacer(1, 0.15*cm),
        Paragraph(info_autonomo.get("email") or "—", estilo_subtitulo),
    ]

    # Columna derecha — PRESUPUESTO O FACTURA y datos bien separados
    titulo_doc = "PRESUPUESTO" if tipo == "presupuesto" else "FACTURA"
    col_derecha = [
        Paragraph(titulo_doc, ParagraphStyle("fact", parent=styles["Normal"],
            fontSize=22, fontName="Helvetica-Bold",
            textColor=colors.HexColor("#2C3E50"), alignment=TA_RIGHT)),
        Spacer(1, 0.4*cm),
        Paragraph(f"Nº {numero_factura}", ParagraphStyle("num", parent=styles["Normal"],
            fontSize=11, fontName="Helvetica-Bold",
            textColor=colors.HexColor("#2C3E50"), alignment=TA_RIGHT)),
        Spacer(1, 0.2*cm),
        Paragraph(f"Fecha: {datos.get('fecha') or datetime.now().strftime('%d/%m/%Y')}",
            ParagraphStyle("fecha_cab", parent=styles["Normal"],
            fontSize=10, textColor=colors.HexColor("#7F8C8D"), alignment=TA_RIGHT)),
    ]

    cabecera = Table(
        [[col_izquierda, col_derecha]],
        colWidths=[10*cm, 7*cm]
    )
    cabecera.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (0,-1), 0),   # sin padding izquierdo — alineado con margen
        ("RIGHTPADDING", (-1,0), (-1,-1), 0), # sin padding derecho
        ("BOTTOMPADDING", (0,0), (-1,-1), 20),
        ("TOPPADDING", (0,0), (-1,-1), 0),
    ]))
    elementos.append(cabecera)

    # Línea separadora
    elementos.append(Table([[""]], colWidths=[17*cm],
        style=TableStyle([("LINEBELOW", (0,0), (-1,-1), 1, colors.HexColor("#2C3E50"))])))
    elementos.append(Spacer(1, 0.5*cm))

    # ── DATOS DEL CLIENTE ─────────────────────────────────
    etiqueta_cliente = "PRESUPUESTO PARA:" if tipo == "presupuesto" else "FACTURAR A:"
    elementos.append(Paragraph(etiqueta_cliente, estilo_subtitulo))
    elementos.append(Spacer(1, 0.2*cm))
    elementos.append(Paragraph(datos.get("cliente_nombre") or "—", estilo_negrita))
    elementos.append(Spacer(1, 0.15*cm))
    elementos.append(Paragraph(datos.get("cliente_direccion") or "—", estilo_normal))
    elementos.append(Spacer(1, 0.5*cm))

    # ── DESCRIPCIÓN DEL TRABAJO ───────────────────────────
    elementos.append(Paragraph("CONCEPTO:", estilo_subtitulo))
    elementos.append(Spacer(1, 0.2*cm))
    elementos.append(Paragraph(datos.get("concepto") or "—", estilo_normal))

    if datos.get("observaciones"):
        elementos.append(Spacer(1, 0.3*cm))
        elementos.append(Paragraph("OBSERVACIONES:", estilo_subtitulo))
        elementos.append(Spacer(1, 0.2*cm))
        elementos.append(Paragraph(
            datos["observaciones"],
            ParagraphStyle("obs", parent=styles["Normal"],
                fontSize=9,
                textColor=colors.HexColor("#7F8C8D"),
                fontName="Helvetica-Oblique")
        ))

    elementos.append(Spacer(1, 0.8*cm))

    # ── TABLA DE LÍNEAS DE FACTURA ────────────────────────
    # Cabecera de la tabla
    filas = [[
        Paragraph("DESCRIPCIÓN", ParagraphStyle("th", parent=styles["Normal"],
            fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph("CANT.", ParagraphStyle("th2", parent=styles["Normal"],
            fontSize=9, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("PRECIO", ParagraphStyle("th3", parent=styles["Normal"],
            fontSize=9, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("IMPORTE", ParagraphStyle("th4", parent=styles["Normal"],
            fontSize=9, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
    ]]

    subtotal = 0

    # Añade materiales como filas
    if datos.get("materiales"):
        for material in datos["materiales"]:
            precio = material.get("precio")
            if precio is None:
                precio_display = "Pendiente"
                importe_display = "Pendiente"
            else:
                precio_display = f"{float(precio):.2f}€"
                importe_display = f"{float(precio):.2f}€"
                subtotal += float(precio)
            filas.append([
                Paragraph(material.get("descripcion") or "—", estilo_normal),
                Paragraph("1", estilo_derecha),
                Paragraph(precio_display, estilo_derecha),
                Paragraph(importe_display, estilo_derecha),
            ])

    # Añade mano de obra
    mostrar_precio_hora = True
    if info_autonomo:
        mostrar_precio_hora = info_autonomo.get("mostrar_precio_hora", True)

    if datos.get("horas") and datos.get("precio_hora"):
        horas = datos["horas"]
        precio_hora = datos["precio_hora"]
        importe_horas = horas * precio_hora
        subtotal += importe_horas
        if mostrar_precio_hora:
            filas.append([
                Paragraph(f"Mano de obra — {datos.get('concepto') or ''}", estilo_normal),
                Paragraph(f"{horas}h", estilo_derecha),
                Paragraph(f"{precio_hora:.2f}€/h", estilo_derecha),
                Paragraph(f"{importe_horas:.2f}€", estilo_derecha),
            ])
        else:
            filas.append([
                Paragraph(f"Mano de obra — {datos.get('concepto') or ''}", estilo_normal),
                Paragraph("", estilo_derecha),
                Paragraph("", estilo_derecha),
                Paragraph(f"{importe_horas:.2f}€", estilo_derecha),
            ])

    # Añade desplazamiento
    if datos.get("desplazamiento") and datos["desplazamiento"] > 0:
        desplazamiento = datos["desplazamiento"]
        subtotal += desplazamiento
        filas.append([
            Paragraph("Desplazamiento", estilo_normal),
            Paragraph("1", estilo_derecha),
            Paragraph(f"{desplazamiento:.2f}€", estilo_derecha),
            Paragraph(f"{desplazamiento:.2f}€", estilo_derecha),
        ])

    # Tabla principal
    tabla = Table(filas, colWidths=[9*cm, 2.5*cm, 2.5*cm, 3*cm])
    tabla.setStyle(TableStyle([
        # Cabecera con fondo oscuro
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2C3E50")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#F8F9FA"), colors.white]),
        ("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#DEE2E6")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
    ]))
    elementos.append(tabla)
    elementos.append(Spacer(1, 0.5*cm))

    # ── TOTALES ───────────────────────────────────────────
    iva_porcentaje_display = f"{int(iva_porcentaje * 100)}%" if iva_porcentaje > 0 else "Exento"
    ajuste_resultado = _calcular_ajuste(subtotal, datos, iva_porcentaje)

    _estilo_tabla = TableStyle([
        ("ALIGN", (1,0), (1,-1), "RIGHT"),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("TEXTCOLOR", (0,0), (-1,-1), colors.HexColor("#2C3E50")),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ])
    _estilo_total_tabla = TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#2C3E50")),
        ("TOPPADDING", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("RIGHTPADDING", (0,0), (-1,-1), 10),
    ])

    if ajuste_resultado is None:
        iva = subtotal * iva_porcentaje
        total = subtotal + iva
        fila_iva = ["IVA:", "Exento"] if iva_porcentaje == 0.0 else [f"IVA ({iva_porcentaje_display}):", f"{iva:.2f}€"]
        tabla_totales = Table([
            ["Subtotal:", f"{subtotal:.2f}€"],
            fila_iva,
        ], colWidths=[14*cm, 3*cm])
        tabla_totales.setStyle(_estilo_tabla)
        elementos.append(tabla_totales)
        elementos.append(Spacer(1, 0.2*cm))
        tabla_total = Table(
            [[Paragraph(f"TOTAL: {total:.2f}€", estilo_total)]],
            colWidths=[17*cm]
        )
        tabla_total.setStyle(_estilo_total_tabla)
        elementos.append(tabla_total)

    elif iva_porcentaje > 0:
        ajuste = ajuste_resultado["ajuste"]
        signo = f"+{ajuste:.2f}" if ajuste >= 0 else f"{ajuste:.2f}"
        tabla_totales = Table([
            ["Subtotal:", f"{subtotal:.2f}€"],
            [f"Ajuste:", f"{signo}€"],
            ["Base imponible:", f"{ajuste_resultado['base_imponible']:.2f}€"],
            [f"IVA ({iva_porcentaje_display}):", f"{ajuste_resultado['cuota_iva']:.2f}€"],
        ], colWidths=[14*cm, 3*cm])
        tabla_totales.setStyle(_estilo_tabla)
        elementos.append(tabla_totales)
        elementos.append(Spacer(1, 0.2*cm))
        tabla_total = Table(
            [[Paragraph(f"TOTAL: {ajuste_resultado['total']:.2f}€", estilo_total)]],
            colWidths=[17*cm]
        )
        tabla_total.setStyle(_estilo_total_tabla)
        elementos.append(tabla_total)

    else:
        ajuste = ajuste_resultado["ajuste"]
        signo = f"+{ajuste:.2f}" if ajuste >= 0 else f"{ajuste:.2f}"
        tabla_totales = Table([
            ["Subtotal:", f"{subtotal:.2f}€"],
            ["Ajuste:", f"{signo}€"],
        ], colWidths=[14*cm, 3*cm])
        tabla_totales.setStyle(_estilo_tabla)
        elementos.append(tabla_totales)
        elementos.append(Spacer(1, 0.2*cm))
        tabla_total = Table(
            [[Paragraph(f"TOTAL: {ajuste_resultado['total']:.2f}€", estilo_total)]],
            colWidths=[17*cm]
        )
        tabla_total.setStyle(_estilo_total_tabla)
        elementos.append(tabla_total)
        elementos.append(Spacer(1, 0.2*cm))
        elementos.append(Paragraph(
            "Operación sujeta a inversión del sujeto pasivo (IVA: 0% — ISP).",
            ParagraphStyle("isp_nota", parent=styles["Normal"],
                fontSize=8, textColor=colors.HexColor("#7F8C8D"), alignment=TA_LEFT)
        ))

    # ── PIE DE PÁGINA ─────────────────────────────────────
    elementos.append(Spacer(1, 1*cm))
    elementos.append(Table([[""]], colWidths=[17*cm],
        style=TableStyle([("LINEABOVE", (0,0), (-1,-1), 0.5, colors.HexColor("#DEE2E6"))])))
    elementos.append(Spacer(1, 0.3*cm))
    if tipo == "presupuesto" and datos.get("validez_dias") and datos["validez_dias"] is not None:
        elementos.append(Spacer(1, 0.3*cm))
        elementos.append(Paragraph(
            f"Este presupuesto tiene una validez de {datos['validez_dias']} días.",
            ParagraphStyle("validez", parent=styles["Normal"],
                fontSize=9, textColor=colors.HexColor("#7F8C8D"), alignment=TA_CENTER)
        ))
    if info_autonomo.get("mostrar_iban") and info_autonomo.get("iban"):
        iban_raw = info_autonomo["iban"].replace(" ", "").upper()
        iban_formateado = " ".join(iban_raw[i:i+4] for i in range(0, len(iban_raw), 4))
        elementos.append(Paragraph(
            f"Transferencia bancaria: {iban_formateado}",
            ParagraphStyle("iban_pie", parent=styles["Normal"],
                fontSize=10, textColor=colors.HexColor("#2C3E50"),
                alignment=TA_LEFT)
        ))
        elementos.append(Spacer(1, 0.2*cm))
    elementos.append(Paragraph(
        "Gracias por confiar en nuestros servicios.",
        ParagraphStyle("pie", parent=styles["Normal"],
            fontSize=9, textColor=colors.HexColor("#7F8C8D"), alignment=TA_CENTER)
    ))

    def marca_agua(canvas_obj, doc_obj):
        if not es_prueba:
            return
        canvas_obj.saveState()
        canvas_obj.setFont("Helvetica-Bold", 70)
        canvas_obj.setFillGray(0.82, alpha=0.18)
        width, height = A4
        canvas_obj.translate(width / 2, height / 2)
        canvas_obj.rotate(45)
        canvas_obj.drawCentredString(0, 0, "PRUEBA")
        canvas_obj.restoreState()

    doc.build(elementos,
              onFirstPage=marca_agua,
              onLaterPages=marca_agua)
    return nombre_archivo