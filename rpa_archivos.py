"""Validación de entradas y descargas, independiente del escritorio Windows."""

import csv
import io
import re
import time
from pathlib import Path

import pdfplumber


def validar_formato_cufe(cufe_original):
    """Devuelve el CUFE normalizado y si contiene 96 caracteres hexadecimales."""
    limpio = (cufe_original or "").strip().replace("-", "").lower()
    return limpio, bool(re.fullmatch(r"[0-9a-f]{96}", limpio))


def leer_entrada(ruta, limite=None, alertas=None):
    # Sin colector se conserva la validación estricta para otros consumidores.
    def alertar(numero, fila, tipo, mensaje, original=""):
        if alertas is None:
            raise ValueError(f"Fila {numero}: {mensaje}")
        alertas.append({"fila": numero, "id": fila.get("id", ""),
                        "cufe_original": original, "cufe": fila.get("cufe", ""),
                        "tipo": tipo, "mensaje": mensaje})

    contenido = Path(ruta).read_text(encoding="utf-8-sig")
    # Compatibilidad con el formato antiguo: cada registro completo entre comillas.
    lineas = [linea for linea in contenido.splitlines() if linea.strip()]
    if not lineas:
        raise ValueError("El CSV de entrada está vacío.")
    if lineas[0].strip().startswith('"id,') or lineas[0].strip().startswith('"id;'):
        contenido = "\n".join(linea.strip()[1:-1] for linea in lineas)
    delimitador = ";" if ";" in contenido.splitlines()[0] else ","
    reader = csv.DictReader(io.StringIO(contenido), delimiter=delimitador)
    requeridas = {"id", "cufe", "nit_consulta", "estado_input"}
    if not requeridas.issubset(reader.fieldnames or []):
        raise ValueError("El CSV necesita id, cufe, nit_consulta y estado_input.")
    registros, vistos = [], set()
    for numero, fila in enumerate(reader, start=2):
        if None in fila or any(fila[campo] is None for campo in requeridas):
            alertar(numero, fila, "ESTRUCTURA_INVALIDA", "Estructura CSV inválida.", fila.get("cufe", ""))
            continue
        original = fila["cufe"]
        fila = {campo: valor.strip() for campo, valor in fila.items()}
        if fila["estado_input"].upper() != "PENDIENTE":
            continue
        cufe, valido = validar_formato_cufe(original)
        fila["cufe"] = cufe
        if not fila["id"] or not valido:
            alertar(numero, fila, "INPUT_INVALIDO", "ID vacío o CUFE inválido: se esperan 96 caracteres hexadecimales; "
                    f"longitud tras limpieza: {len(cufe)}.", original)
            continue
        if not re.fullmatch(r"\d+(?:-\d)?", fila["nit_consulta"]):
            alertar(numero, fila, "NIT_INVALIDO", "NIT inválido; debe conservarse como texto.", original)
            continue
        fila["cufe"] = fila["cufe"].lower()
        if fila["cufe"] in vistos:
            alertar(numero, fila, "CUFE_DUPLICADO", "CUFE duplicado: se omite; se conserva la primera fila válida pendiente.", original)
            continue
        if original != cufe and alertas is not None:
            alertar(numero, fila, "CUFE_NORMALIZADO", "CUFE normalizado: espacios externos, guiones o mayúsculas.", original)
        vistos.add(fila["cufe"])
        registros.append(fila)
    if limite is not None and limite < 1:
        raise ValueError("El límite de registros debe ser mayor que cero.")
    return registros if limite is None else registros[:limite]


def validar_pdf(ruta, cufe, password):
    ruta = Path(ruta)
    with ruta.open("rb") as archivo:
        if archivo.read(5) != b"%PDF-":
            raise ValueError("El archivo descargado no tiene una cabecera PDF válida.")
    with pdfplumber.open(ruta, password=password) as pdf:
        if not pdf.pages:
            raise ValueError("PDF sin páginas.")
        texto = "".join(p.extract_text() or "" for p in pdf.pages)
        compacto = re.sub(r"\s+", "", texto.lower())
        if texto.strip() and cufe.lower() not in compacto:
            raise ValueError("El CUFE del PDF no coincide con el registro consultado.")
        # En documentos escaneados se comprobará el CUFE mediante OCR al extraer.
    return ruta


def snapshot_pdfs(carpeta):
    snapshot = {}
    for ruta in Path(carpeta).glob("*.pdf"):
        try:
            stat = ruta.stat()
            snapshot[ruta] = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            continue
    return snapshot


def candidatos_pdf(carpeta, cufe):
    return [p for p in Path(carpeta).glob("*.pdf") if cufe.lower() in p.name.lower()]


def esperar_pdf(carpeta, snapshot, cufe, password, timeout=60):
    """Espera solo el CUFE actual, sin bloquearse por .part de otras descargas."""
    fin = time.monotonic() + timeout
    estables = {}
    ultimo_error = "No apareció un PDF nuevo para el CUFE actual."
    while time.monotonic() < fin:
        for ruta in candidatos_pdf(carpeta, cufe):
            try:
                stat = ruta.stat()
                firma = (stat.st_mtime_ns, stat.st_size)
                if firma == snapshot.get(ruta):
                    continue
                if ruta.with_suffix(ruta.suffix + ".part").exists():
                    continue
                if not firma[1] or estables.get(ruta) != firma:
                    estables[ruta] = firma
                    continue
                return validar_pdf(ruta, cufe, password)
            except (OSError, ValueError, pdfplumber.utils.exceptions.PdfminerException) as error:
                ultimo_error = f"{type(error).__name__}: {error}"
        time.sleep(.5)
    raise TimeoutError(f"Descarga no completada en {timeout}s. {ultimo_error}")


def recuperar_pdf(carpeta_proyecto, carpeta_descargas, cufe, password):
    errores = []
    for carpeta in (carpeta_proyecto, carpeta_descargas):
        for ruta in sorted(candidatos_pdf(carpeta, cufe), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                return validar_pdf(ruta, cufe, password)
            except (OSError, ValueError, pdfplumber.utils.exceptions.PdfminerException) as error:
                errores.append(f"{ruta.name}: {type(error).__name__}: {error}")
    raise FileNotFoundError("No hay PDF válido para este CUFE. " + " | ".join(errores))
