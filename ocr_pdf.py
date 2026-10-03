"""OCR local de páginas y tablas con cuadrícula del formato DIAN."""

import csv
import difflib
import io
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np


def ejecutar_ocr(imagen, formato="txt", psm=6):
    if formato == "txt" and not (np.asarray(imagen.convert("L")) < 180).any():
        return ""
    ejecutable = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not ejecutable:
        candidatos = [Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
                      Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe"]
        ejecutable = next((str(p) for p in candidatos if p.is_file()), None)
    if not ejecutable:
        raise RuntimeError("Instala Tesseract OCR o configura TESSERACT_CMD.")
    idioma = os.environ.get("OCR_LANG", "spa")
    modelos = Path(__file__).resolve().parent / "tools" / "tessdata"
    opciones = ["--tessdata-dir", str(modelos)] if (modelos / f"{idioma}.traineddata").exists() else []
    salida = ["-c", "tessedit_create_tsv=1"] if formato == "tsv" else []
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    resultado = subprocess.run(
        [ejecutable, "stdin", "stdout", "-l", idioma,
         "--psm", str(psm), *opciones, *salida],
        input=buffer.getvalue(), capture_output=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if resultado.returncode:
        raise RuntimeError(f"Tesseract falló: {resultado.stderr.decode('utf-8', errors='replace').strip()}")
    return resultado.stdout.decode("utf-8", errors="replace")


def centros_lineas(indices):
    grupos = []
    for indice in indices:
        if not grupos or indice > grupos[-1][-1] + 1:
            grupos.append([int(indice)])
        else:
            grupos[-1].append(int(indice))
    return [int(sum(grupo) / len(grupo)) for grupo in grupos]


class PaginaOCR:
    """Adaptador para reutilizar el extractor sin modificar los PDF originales."""

    def __init__(self, pagina):
        self.imagen = pagina.to_image(resolution=300).original.convert("RGB")
        from PIL import Image
        pixeles = np.asarray(self.imagen).astype(np.int16)
        verde = (pixeles[:, :, 1] > pixeles[:, :, 0] + 8) & (pixeles[:, :, 1] > pixeles[:, :, 2] + 3)
        pixeles[verde] = 255
        self.lectura = Image.fromarray(pixeles.astype(np.uint8))
        self.texto = ejecutar_ocr(self.lectura)

    def extract_text(self):
        return self.texto

    def extract_tables(self):
        tsv = ejecutar_ocr(self.lectura, formato="tsv", psm=6)
        palabras = list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))
        anclas = [p for p in palabras if (p.get("text") or "").strip().lower() == "cantidad"]
        if not anclas:
            # La distribución de dos columnas puede ocultar la tabla en el OCR global.
            offset = self.lectura.height // 2
            region = self.lectura.crop((0, offset, self.lectura.width, self.lectura.height))
            tsv = ejecutar_ocr(region, formato="tsv", psm=6)
            palabras = list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))
            anclas = [p for p in palabras if (p.get("text") or "").strip().lower() == "cantidad"]
            for ancla in anclas:
                ancla["top"] = str(int(ancla["top"]) + offset)
        if not anclas:
            return []
        pixeles = np.asarray(self.imagen).astype(np.int16)
        # Líneas negras o verdes; el fondo verde claro del encabezado se excluye.
        oscuro = pixeles.max(axis=2) < 180
        verde = ((pixeles[:, :, 1] > pixeles[:, :, 0] + 35)
                 & (pixeles[:, :, 1] > pixeles[:, :, 2] + 15)
                 & (pixeles[:, :, 0] < 120))
        mascara = oscuro | verde
        tablas = []
        for ancla in anclas:
            top = int(ancla["top"])
            bottom = top + int(ancla["height"])
            banda = slice(max(0, top - 15), min(self.imagen.height, bottom + 16))
            lineas = verde if verde[banda].any() else oscuro
            xs = centros_lineas(np.flatnonzero(lineas[banda].mean(axis=0) > .85))
            if len(xs) < 7:
                raise ValueError("OCR: no se detectó la cuadrícula de productos; revisar imagen.")
            # Conservar únicamente bordes próximos a las columnas de la tabla.
            ys = centros_lineas(np.flatnonzero(mascara[:, xs[0]:xs[-1] + 1].mean(axis=1) > .85))
            superior = [y for y in ys if y <= top]
            inferiores = [y for y in ys if y > bottom]
            if not superior or not inferiores:
                raise ValueError("OCR: no se detectaron los bordes del encabezado.")
            y_header = superior[-1]
            y_inicio = inferiores[0]
            bordes = np.flatnonzero(mascara[max(0, y_inicio-1):y_inicio+2].any(axis=0))
            if len(bordes) and bordes[0] < xs[0] - 10:
                xs.insert(0, int(bordes[0]))
            if len(bordes) and bordes[-1] > xs[-1] + 10:
                xs.append(int(bordes[-1]))
            encabezados = []
            for izquierda, derecha in zip(xs, xs[1:]):
                celda = self.lectura.crop((izquierda + 3, y_header + 3, derecha - 3, y_inicio - 3))
                encabezados.append(ejecutar_ocr(celda).strip())
            from procesamiento_pdf import normalizar
            for i, encabezado in enumerate(encabezados):
                candidatos = difflib.get_close_matches(
                    normalizar(encabezado), ["codigo", "descripcion", "cantidad", "precio unitario"],
                    n=1, cutoff=.8,
                )
                if candidatos:
                    encabezados[i] = candidatos[0]
            # Algunos motores omiten tildes; el extractor normaliza encabezados.
            tabla = [encabezados]
            previo = y_inicio
            for siguiente in inferiores[1:]:
                # Una interrupción de los bordes laterales marca el final de la tabla.
                if mascara[previo:siguiente, max(0, xs[0]-2):xs[0]+3].any(axis=1).mean() < .9:
                    break
                fila = []
                for izquierda, derecha in zip(xs, xs[1:]):
                    celda = self.lectura.crop((izquierda + 6, previo + 6, derecha - 6, siguiente - 6))
                    # El margen inferior evita restos de la cuadrícula en celdas vacías.
                    if encabezados[len(fila)] == "codigo":
                        celda = self.lectura.crop((izquierda + 6, previo + 12, derecha - 6, siguiente - 12))
                    fila.append(ejecutar_ocr(celda).strip().replace("\r\n", "\n"))
                tabla.append(fila)
                previo = siguiente
            if len(tabla) == 1:
                raise ValueError("OCR: tabla de productos sin filas reconocibles.")
            tablas.append(tabla)
        return tablas
