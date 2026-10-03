"""Extrae facturas DIAN a un DataFrame por PDF y un CSV consolidado."""

import argparse
import csv
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import pdfplumber

BASE_DIR = Path(__file__).resolve().parent
CSV_OUTPUT_DIR = BASE_DIR / "data" / "csv"
NOMBRE_CONSOLIDADO = "facturas_consolidadas.csv"
COLUMNAS = [
    "numero_factura", "fecha_emision", "nit_emisor",
    "codigo", "descripcion", "cantidad", "precio_unitario",
]


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "")
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).lower().split())


def numero_decimal(texto):
    """Convierte números colombianos sin perder precisión monetaria."""
    valor = re.sub(r"[\s$]", "", texto or "")
    if "," in valor:
        valor = valor.replace(".", "").replace(",", ".")
    try:
        numero = Decimal(valor)
        if not numero.is_finite():
            raise InvalidOperation
        return numero
    except InvalidOperation as error:
        raise ValueError(f"Número inválido en tabla: {texto!r}") from error


def extraer_dataframe(pdf_path, password=None, ocr="auto"):
    """Lee todas las páginas. Conserva códigos y NIT como texto.

    Las descripciones conservan los saltos de línea originales de la celda.
    OCR: auto para páginas sin texto, siempre para todas, nunca para desactivarlo.
    """
    pdf_path = Path(pdf_path)
    if ocr not in {"auto", "siempre", "nunca"}:
        raise ValueError("Modo OCR inválido.")
    with pdfplumber.open(pdf_path, password=password or "") as documento:
        paginas = []
        paginas_ocr = 0
        for pagina in documento.pages:
            texto_pagina = pagina.extract_text() or ""
            if ocr == "siempre" or (ocr == "auto" and not texto_pagina.strip()):
                from ocr_pdf import PaginaOCR
                print(f"OCR: {pdf_path.name}, página {pagina.page_number}")
                pagina = PaginaOCR(pagina)
                paginas_ocr += 1
            paginas.append(pagina)
        textos = [pagina.extract_text() or "" for pagina in paginas]
        texto = normalizar("\n".join(textos))
        if not texto:
            raise ValueError("PDF sin texto extraíble; requiere OCR.")
        if re.fullmatch(r"[a-fA-F0-9]{96}", pdf_path.stem):
            compacto = re.sub(r"\s+", "", "".join(textos).lower())
            if pdf_path.stem.lower() not in compacto:
                raise ValueError("El CUFE del contenido no coincide con el nombre del PDF; revisar OCR o descarga.")
        seccion_documento = re.search(
            r"datos del documento\s+(.*?)datos del emisor\s*/\s*vendedor", texto
        )
        seccion_emisor = re.search(
            r"datos del emisor\s*/\s*vendedor\s+(.*?)datos del adquir", texto
        )
        if not seccion_documento or not seccion_emisor:
            raise ValueError("No se encontraron las secciones de documento y emisor.")
        patrones = {
            "numero_factura": (r"numero de factura\s*:\s*(\S+)", seccion_documento[1]),
            "fecha_emision": (r"fecha de emision\s*:\s*(\d{2}/\d{2}/\d{4})", seccion_documento[1]),
            "nit_emisor": (r"nit del emisor\s*:\s*([\d.]+(?:\s*-\s*\d+)?)", seccion_emisor[1]),
        }
        datos = {}
        for campo, (patron, seccion) in patrones.items():
            coincidencia = re.search(patron, seccion)
            if not coincidencia:
                raise ValueError(f"No se encontró {campo}.")
            datos[campo] = coincidencia[1]
        # Recuperar mayúsculas del identificador de factura del texto original.
        original = "\n".join(textos)
        numero = re.search(r"N[uú]mero de Factura\s*:\s*(\S+)", original, re.I)
        if numero:
            datos["numero_factura"] = numero[1]
        productos = []
        for pagina in paginas:
            for tabla in pagina.extract_tables():
                indices = None
                for fila in tabla:
                    encabezados = [normalizar(celda) for celda in fila]
                    requeridos = ["codigo", "descripcion", "cantidad", "precio unitario"]
                    if all(nombre in encabezados for nombre in requeridos):
                        indices = {nombre: encabezados.index(nombre) for nombre in requeridos}
                        continue
                    if indices is None:
                        continue
                    if not any(fila):
                        continue
                    if not re.fullmatch(r"\d+", (fila[0] or "").strip()):
                        raise ValueError("Fila de producto sin número; revisar formato de tabla.")
                    producto = {nombre: (fila[i] or "").strip() for nombre, i in indices.items()}
                    if not producto["descripcion"]:
                        raise ValueError("Producto sin descripción.")
                    productos.append({
                        **datos,
                        "codigo": producto["codigo"],
                        "descripcion": producto["descripcion"],
                        "cantidad": numero_decimal(producto["cantidad"]),
                        "precio_unitario": numero_decimal(producto["precio unitario"]),
                    })
        if not productos:
            raise ValueError("No se encontró una tabla de productos compatible.")
    dataframe = pd.DataFrame(productos, columns=COLUMNAS)
    dataframe.attrs["metodo_extraccion"] = (
        "OCR" if paginas_ocr == len(paginas) else "MIXTO" if paginas_ocr else "TEXTO"
    )
    return dataframe


def procesar_pdf(pdf_path, password=None, output_dir=CSV_OUTPUT_DIR, ocr="auto", salida="consolidado"):
    """Exporta al consolidado o a un CSV individual por CUFE."""
    if salida not in {"consolidado", "individual"}:
        raise ValueError("Salida inválida: usa consolidado o individual.")
    dataframe = extraer_dataframe(pdf_path, password=password, ocr=ocr)
    dataframe.insert(0, "cufe", Path(pdf_path).stem)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if salida == "individual":
        destino = output_dir / "individuales" / (Path(pdf_path).stem + ".csv")
        guardar_consolidado(dataframe, destino)
        return dataframe, destino
    destino = output_dir / NOMBRE_CONSOLIDADO
    if destino.exists():
        anterior = pd.read_csv(destino, sep=";", dtype=str, keep_default_na=False)
        if list(anterior.columns) != ["cufe", *COLUMNAS]:
            raise ValueError("El CSV consolidado existente tiene columnas incompatibles.")
        anterior = anterior.loc[anterior["cufe"] != Path(pdf_path).stem]
        consolidado = pd.concat([anterior, dataframe], ignore_index=True)
    else:
        consolidado = dataframe
    guardar_consolidado(consolidado, destino)
    return dataframe, destino


def guardar_consolidado(dataframe, destino):
    """Escribe primero un temporal para evitar un CSV incompleto ante fallos."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(".csv.tmp")
    dataframe.to_csv(temporal, index=False, sep=";", encoding="utf-8-sig")
    temporal.replace(destino)


def leer_passwords(input_csv):
    """Relaciona CUFE y NIT, incluyendo el CSV con filas entre comillas."""
    lineas = Path(input_csv).read_text(encoding="utf-8-sig").splitlines()
    lineas = [linea.strip() for linea in lineas if linea.strip()]
    if not lineas:
        return {}
    if lineas[0].startswith('"') and lineas[0].endswith('"'):
        lineas = [linea[1:-1] if linea.startswith('"') and linea.endswith('"') else linea for linea in lineas]
    delimitador = ";" if ";" in lineas[0] else ","
    return {fila["cufe"].strip(): fila["nit_consulta"].strip()
            for fila in csv.DictReader(lineas, delimiter=delimitador)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=BASE_DIR / "data" / "pdf")
    parser.add_argument("--output-dir", type=Path, default=CSV_OUTPUT_DIR)
    parser.add_argument("--input-csv", type=Path, default=BASE_DIR / "data" / "input" / "cufes.csv")
    parser.add_argument("--ocr", choices=["auto", "siempre", "nunca"], default="auto")
    parser.add_argument("--salida", choices=["consolidado", "individual"], default="consolidado")
    args = parser.parse_args()
    passwords = leer_passwords(args.input_csv) if args.input_csv.exists() else {}
    archivos = sorted(args.input_dir.glob("*.pdf"))
    if not archivos:
        raise SystemExit("No se encontraron PDF en la carpeta indicada.")
    errores = 0
    dataframes = []
    for archivo in archivos:
        try:
            dataframe, destino = procesar_pdf(archivo, passwords.get(archivo.stem), args.output_dir, ocr=args.ocr, salida=args.salida)
            dataframes.append(dataframe)
            print(f"OK: {archivo.name}: {len(dataframe)} productos -> {destino}")
        except Exception as error:
            errores += 1
            print(f"ERROR: {archivo.name}: {type(error).__name__}: {error}")
    if dataframes and args.salida == "consolidado":
        destino = args.output_dir / NOMBRE_CONSOLIDADO
        print(f"CSV consolidado: {destino} ({sum(len(df) for df in dataframes)} productos exportados en esta ejecución)")
    elif not dataframes:
        print("No se generaron CSV: ningún PDF pudo extraerse.")
    print(f"Procesados: {len(archivos)}; exitosos: {len(archivos) - errores}; errores: {errores}")
    if errores:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
