import os
import time
import requests
import json
from dotenv import load_dotenv

load_dotenv()
HOLDED_API_KEY = os.getenv("HOLDED_API_KEY")

HEADERS = {
    "key": HOLDED_API_KEY,
    "Content-Type": "application/json"
}

BASE_URL = "https://api.holded.com/api/invoicing/v1"

def buscar_contacto(nombre):
    """Busca contacto por nombre — busca coincidencia parcial"""
    url = f"{BASE_URL}/contacts"
    respuesta = requests.get(url, headers=HEADERS)

    if respuesta.status_code == 200:
        contactos = respuesta.json()
        # Primero busca coincidencia exacta
        for contacto in contactos:
            if contacto.get("name", "").lower() == nombre.lower():
                print(f"Contacto encontrado exacto: {contacto['name']} ({contacto['id']})")
                return contacto["id"]
        # Si no, busca si el nombre del contacto está contenido en lo que dijo el usuario
        # Por ejemplo "Blai Grau" está dentro de "Blai Grau C/ Bachserstrasse 8"
        for contacto in contactos:
            nombre_contacto = contacto.get("name", "").lower()
            if nombre_contacto and nombre_contacto in nombre.lower():
                print(f"Contacto encontrado parcial: {contacto['name']} ({contacto['id']})")
                return contacto["id"]
    return None

def crear_contacto(nombre):
    """Crea un contacto nuevo en Holded — devuelve su ID"""
    url = f"{BASE_URL}/contacts"
    datos = {"name": nombre}
    respuesta = requests.post(url, headers=HEADERS, json=datos)
    print(f"Crear contacto respuesta: {respuesta.status_code} — {respuesta.text}")

    if respuesta.status_code == 200:
        resultado = respuesta.json()
        # Holded puede devolver el ID en diferentes campos
        return resultado.get("id") or resultado.get("contactId") or resultado.get("_id")
    return None

def obtener_o_crear_contacto(nombre):
    """Busca el contacto y si no existe lo crea"""
    contacto_id = buscar_contacto(nombre)
    if contacto_id:
        return contacto_id

    print(f"Contacto no encontrado, creando: {nombre}")
    contacto_id = crear_contacto(nombre)
    print(f"Contacto creado con ID: {contacto_id}")
    return contacto_id

def crear_factura(datos):
    """Crea una factura en Holded con los datos del bot"""

    contacto_id = obtener_o_crear_contacto(datos["cliente"])
    if not contacto_id:
        return None, "No se pudo crear el contacto en Holded"

    # Convierte la fecha a Unix timestamp
    try:
        from datetime import datetime
        if datos.get("fecha"):
            fecha_dt = datetime.strptime(datos["fecha"], "%d/%m/%Y")
        else:
            fecha_dt = datetime.now()
        fecha_unix = int(time.mktime(fecha_dt.timetuple()))
    except Exception:
        fecha_unix = int(time.time())

    # Construye los items de la factura
    items = []

    if datos.get("materiales"):
        for material in datos["materiales"]:
            items.append({
                "name": material["descripcion"],
                "units": 1,
                "price": material["precio"],
                "tax": 21
            })

    if datos.get("horas") and datos.get("precio_hora"):
        items.append({
            "name": f"Mano de obra — {datos['concepto']}",
            "units": datos["horas"],
            "price": datos["precio_hora"],
            "tax": 21
        })

    if datos.get("desplazamiento") and datos["desplazamiento"] > 0:
        items.append({
            "name": "Desplazamiento",
            "units": 1,
            "price": datos["desplazamiento"],
            "tax": 21
        })

    factura = {
        "contactId": contacto_id,
        "date": fecha_unix,
        "notes": f"Factura generada automáticamente — {datos['concepto']}",
        "items": items
    }

    print(f"Enviando factura a Holded: {json.dumps(factura, indent=2)}")

    url = f"{BASE_URL}/documents/invoice"
    respuesta = requests.post(url, headers=HEADERS, json=factura)
    print(f"Respuesta factura: {respuesta.status_code} — {respuesta.text}")

    if respuesta.status_code == 200:
        resultado = respuesta.json()
        factura_id = resultado.get("id") or resultado.get("invoiceId")
        return factura_id, None
    else:
        return None, f"Error Holded: {respuesta.status_code} — {respuesta.text}"