"""Consulta DIAN, descarga y extracción con estados verificables por registro."""

import argparse
import csv
import ctypes
import io
import json
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pandas as pd
import numpy as np
import pyautogui
import pygetwindow as gw
import pyperclip

from ocr_pdf import ejecutar_ocr
from procesamiento_pdf import CSV_OUTPUT_DIR, NOMBRE_CONSOLIDADO, normalizar, procesar_pdf
from rpa_archivos import esperar_pdf, leer_entrada, recuperar_pdf, snapshot_pdfs, validar_pdf

BASE_DIR = Path(__file__).resolve().parent
DIAN_URL = "https://catalogo-vpfe.dian.gov.co/User/SearchDocument"
INPUT_CSV = BASE_DIR / "data" / "input" / "cufes.csv"
PDF_OUTPUT_DIR = BASE_DIR / "data" / "pdf"
LOG_DIR = BASE_DIR / "logs"
RESULTADOS_RPA_CSV = LOG_DIR / "resultados_rpa.csv"
DEBUG_DIR = BASE_DIR / "debug"
DOWNLOADS_DIR = Path.home() / "Downloads"
MAX_CUFES = 2
DIAN_HWND = None
POS_CAMPO_CUFE = {"x": 581, "y": 481}
TABS_HASTA_NIT = 1
TIEMPO_MAX_PAGINA = 30
TIEMPO_MAX_DESCARGA = 60
MAX_INTENTOS = 2

try:
    ctypes.windll.user32.SetProcessDPIAware()
except AttributeError:
    pass
pyautogui.FAILSAFE = True
pyautogui.PAUSE = .2

COLUMNAS_LOG = [
    "id", "cufe", "nit_consulta", "estado_rpa", "pdf_path", "duracion_segundos",
    "fecha_proceso", "error", "estado_extraccion", "csv_path", "error_extraccion",
    "etapa", "intentos", "productos", "metodo_extraccion", "origen_pdf", "advertencia",
]


class ValidacionSeguridadPendiente(RuntimeError):
    pass


class IntervencionHumanaPendiente(RuntimeError):
    pass


def crear_carpetas():
    for carpeta in (PDF_OUTPUT_DIR, CSV_OUTPUT_DIR, LOG_DIR, DEBUG_DIR):
        carpeta.mkdir(parents=True, exist_ok=True)


def validar_salidas(salida="consolidado", registros=()):
    for carpeta in (PDF_OUTPUT_DIR, CSV_OUTPUT_DIR, LOG_DIR):
        prueba = carpeta / f".rpa_escritura_{uuid4().hex}.tmp"
        try:
            prueba.write_text("", encoding="utf-8")
        finally:
            prueba.unlink(missing_ok=True)
    destinos = ([CSV_OUTPUT_DIR / NOMBRE_CONSOLIDADO] if salida == "consolidado" else
                [CSV_OUTPUT_DIR / "individuales" / (r["cufe"] + ".csv") for r in registros])
    for ruta in [RESULTADOS_RPA_CSV, *destinos]:
        if ruta.exists():
            try:
                with ruta.open("r+", encoding="utf-8-sig"):
                    pass
            except PermissionError as error:
                raise PermissionError(f"Cierra el archivo en Excel u otra aplicación antes de ejecutar: {ruta}") from error


def leer_cufes():
    return leer_entrada(INPUT_CSV, MAX_CUFES)


def guardar_resultados(resultados):
    temporal = RESULTADOS_RPA_CSV.with_suffix(".csv.tmp")
    with temporal.open("w", newline="", encoding="utf-8-sig") as archivo:
        writer = csv.DictWriter(archivo, fieldnames=COLUMNAS_LOG, delimiter=";")
        writer.writeheader()
        writer.writerows(resultados)
    temporal.replace(RESULTADOS_RPA_CSV)


def capturar_error(cufe, etapa):
    ruta = DEBUG_DIR / f"{cufe[:12]}_{etapa}_{datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        pyautogui.screenshot().save(ruta)
        return str(ruta)
    except OSError:
        return ""


def obtener_hwnd(ventana):
    return getattr(ventana, "_hWnd", None)


def es_firefox(ventana):
    if ventana is None:
        return False
    titulo = (ventana.title or "").strip().lower()
    return titulo.endswith("mozilla firefox") or titulo == "firefox"


def encontrar_ventana_dian():
    ventanas = [w for w in gw.getAllWindows() if es_firefox(w)]
    for ventana in ventanas:
        if obtener_hwnd(ventana) == DIAN_HWND:
            return ventana
    return next((w for w in ventanas if "dian" in w.title.lower()), None)


def activar_ventana(ventana):
    if ventana is None:
        return False
    try:
        if ventana.isMinimized:
            ventana.restore()
        ventana.maximize()
        ventana.activate()
        time.sleep(.5)
        if obtener_hwnd(gw.getActiveWindow()) == obtener_hwnd(ventana):
            return True
    except gw.PyGetWindowException:
        pass
    # Windows puede rechazar activate() por la restricción de primer plano.
    hwnd = obtener_hwnd(ventana)
    user32 = ctypes.windll.user32
    hilo_actual = ctypes.windll.kernel32.GetCurrentThreadId()
    hilo_foco = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    conectado = False
    try:
        if hilo_foco and hilo_foco != hilo_actual:
            conectado = bool(user32.AttachThreadInput(hilo_actual, hilo_foco, True))
        user32.SetForegroundWindow(hwnd)
    finally:
        if conectado:
            user32.AttachThreadInput(hilo_actual, hilo_foco, False)
    time.sleep(.5)
    return obtener_hwnd(gw.getActiveWindow()) == hwnd


def enfocar_dian():
    global DIAN_HWND
    ventana = encontrar_ventana_dian()
    if activar_ventana(ventana):
        DIAN_HWND = obtener_hwnd(ventana)
        return True
    return False


def exigir_foco():
    if not enfocar_dian():
        raise RuntimeError("No se pudo activar y verificar la ventana Firefox de DIAN.")


def pegar_texto(texto):
    pyperclip.copy(str(texto))
    pyautogui.hotkey("ctrl", "v")
    time.sleep(.3)


def leer_seleccion():
    pyperclip.copy("")
    pyautogui.hotkey("ctrl", "c")
    time.sleep(.2)
    return pyperclip.paste().strip()


def verificar_campo(texto):
    pyautogui.hotkey("ctrl", "a")
    if leer_seleccion() != texto:
        raise RuntimeError("El contenido del campo no coincide; foco o coordenadas incorrectos.")


def limpiar_y_pegar(texto):
    pyautogui.hotkey("ctrl", "a")
    pegar_texto(texto)
    verificar_campo(texto)


def verificar_origen():
    ventana = gw.getActiveWindow()
    if obtener_hwnd(ventana) != DIAN_HWND or not es_firefox(ventana):
        raise RuntimeError("El foco dejó de pertenecer a Firefox DIAN.")
    pyautogui.hotkey("ctrl", "l")
    url = leer_seleccion()
    pyautogui.press("esc")
    if urlparse(url).hostname != "catalogo-vpfe.dian.gov.co":
        raise RuntimeError(f"La pestaña activa no corresponde al sitio DIAN: {url}")


def leer_pantalla():
    pyautogui.failSafeCheck()
    ventana = gw.getActiveWindow()
    if obtener_hwnd(ventana) != DIAN_HWND:
        exigir_foco()
    return reconocer_pantalla(pyautogui.screenshot())


def reconocer_pantalla(imagen):
    from PIL import Image
    pixeles = np.asarray(imagen.convert("RGB")).astype(np.int16)
    # El fondo verde brillante de Buscar/Aceptar oculta el texto al segmentador.
    fondo = (pixeles[:, :, 1] > 170) & (pixeles[:, :, 1] > pixeles[:, :, 0] + 25)
    def grupos(indices):
        salida = []
        for valor in indices:
            if not salida or valor > salida[-1][1] + 1:
                salida.append([int(valor), int(valor)])
            else:
                salida[-1][1] = int(valor)
        return salida
    # Aceptar tiene letras blancas: conservarlas como negras antes de blanquear
    # el fondo. Solo aplicar dentro de rectángulos verdes densos de botones.
    for arriba, abajo in grupos(np.flatnonzero(fondo.sum(axis=1) >= 30)):
        alto = abajo - arriba + 1
        if not 20 <= alto <= 100:
            continue
        columnas = np.flatnonzero(fondo[arriba:abajo+1].sum(axis=0) >= alto * .1)
        for izquierda, derecha in grupos(columnas):
            region = fondo[arriba:abajo+1, izquierda:derecha+1]
            if 60 <= derecha - izquierda + 1 <= 400 and region.mean() > .35:
                celda = pixeles[arriba:abajo+1, izquierda:derecha+1]
                claras = celda.min(axis=2) > 220
                celda[claras] = 0
    pixeles[fondo] = 255
    imagen = Image.fromarray(pixeles.astype(np.uint8))
    imagen = imagen.resize((imagen.width * 2, imagen.height * 2))
    tsv = ejecutar_ocr(imagen, formato="tsv", psm=11)
    palabras = []
    for palabra in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        if palabra.get("text", "").strip() and float(palabra["conf"]) >= 30:
            for campo in ("left", "top", "width", "height"):
                palabra[campo] = str(int(palabra[campo]) // 2)
            palabras.append(palabra)
    return palabras


def ubicar_texto(palabras, texto, exacto=False):
    buscado = normalizar(texto).split()
    for inicio in range(len(palabras)):
        segmento = palabras[inicio:inicio + len(buscado)]
        if [normalizar(p["text"]).strip(".:,") for p in segmento] != buscado:
            continue
        if len({(p["block_num"], p["par_num"], p["line_num"]) for p in segmento}) != 1:
            continue
        if exacto:
            linea = (segmento[0]["block_num"], segmento[0]["par_num"], segmento[0]["line_num"])
            palabras_linea = [p for p in palabras if (p["block_num"], p["par_num"], p["line_num"]) == linea]
            if len(palabras_linea) != len(buscado):
                continue
        izquierda = min(int(p["left"]) for p in segmento)
        derecha = max(int(p["left"]) + int(p["width"]) for p in segmento)
        arriba = min(int(p["top"]) for p in segmento)
        abajo = max(int(p["top"]) + int(p["height"]) for p in segmento)
        return {"x": (izquierda + derecha) // 2, "y": (arriba + abajo) // 2}
    return None


def comprobar_mensajes(palabras):
    pantalla = normalizar(" ".join(p["text"] for p in palabras))
    for mensaje in ("el campo de seguridad no esta completo", "falta token de validacion de captcha"):
        if mensaje in pantalla:
            raise ValidacionSeguridadPendiente(f"DIAN: {mensaje}; todavía no está lista la validación de seguridad.")
    for mensaje in ("verifique que es humano", "verifique que es un ser humano"):
        if mensaje in pantalla:
            raise IntervencionHumanaPendiente("DIAN requiere verificación humana en Firefox; no se resolverá automáticamente.")
    for mensaje in ("no se encontraron", "documento no encontrado", "no existe el documento", "acceso denegado"):
        if mensaje in pantalla:
            raise RuntimeError(f"La página indica: {mensaje}.")


def esperar_elemento(texto, timeout=TIEMPO_MAX_PAGINA):
    fin = time.monotonic() + timeout
    while time.monotonic() < fin:
        palabras = leer_pantalla()
        comprobar_mensajes(palabras)
        posicion = ubicar_texto(palabras, texto, exacto=normalizar(texto) in {"buscar", "aceptar"})
        if posicion:
            return posicion
        time.sleep(.5)
    raise TimeoutError(f"No se encontró el elemento visible '{texto}' en {timeout}s.")


def click_en(posicion, descripcion):
    if obtener_hwnd(gw.getActiveWindow()) != DIAN_HWND:
        raise RuntimeError(f"Se perdió el foco antes de pulsar {descripcion}.")
    ancho, alto = pyautogui.size()
    if not (0 <= posicion["x"] < ancho and 0 <= posicion["y"] < alto):
        raise ValueError(f"Coordenadas fuera de pantalla: {descripcion}.")
    pyautogui.click(posicion["x"], posicion["y"])
    time.sleep(.3)


def abrir_firefox():
    global DIAN_HWND
    ejecutable = shutil.which("firefox")
    if not ejecutable:
        ejecutable = next((str(p) for p in [Path("C:/Program Files/Mozilla Firefox/firefox.exe"),
                         Path("C:/Program Files (x86)/Mozilla Firefox/firefox.exe")] if p.exists()), None)
    if not ejecutable:
        raise FileNotFoundError("No se encontró Firefox instalado.")
    anteriores = {obtener_hwnd(w) for w in gw.getAllWindows() if es_firefox(w)}
    subprocess.Popen([ejecutable, "-new-window", DIAN_URL])
    fin = time.monotonic() + TIEMPO_MAX_PAGINA
    while time.monotonic() < fin:
        nueva = next((w for w in gw.getAllWindows() if es_firefox(w)
                      and obtener_hwnd(w) not in anteriores and "dian" in w.title.lower()), None)
        if nueva is not None and activar_ventana(nueva):
            DIAN_HWND = obtener_hwnd(nueva)
            pyautogui.hotkey("ctrl", "0")
            esperar_elemento("CUFE")
            verificar_origen()
            return
        time.sleep(.5)
    raise TimeoutError("No se encontró la ventana DIAN después de abrir Firefox.")


def navegar_a_busqueda_misma_ventana():
    exigir_foco()
    pyautogui.hotkey("ctrl", "l")
    pegar_texto(DIAN_URL)
    pyautogui.press("enter")
    pyautogui.hotkey("ctrl", "0")
    esperar_elemento("CUFE")
    esperar_elemento("Buscar")
    verificar_origen()


def consultar_factura(cufe, nit):
    exigir_foco()
    verificar_origen()
    esperar_elemento("CUFE")
    click_en(POS_CAMPO_CUFE, "campo CUFE calibrado")
    limpiar_y_pegar(cufe)
    pyautogui.press("tab", presses=TABS_HASTA_NIT, interval=.2)
    esperar_elemento("NIT")
    limpiar_y_pegar(nit)
    # Comprobar ambos campos evita confundir el CUFE con el NIT por un Tab incorrecto.
    pyautogui.hotkey("shift", "tab")
    verificar_campo(cufe)
    pyautogui.press("tab")
    verificar_campo(nit)
    buscar = esperar_elemento("Buscar")
    click_en(buscar, "Buscar")
    esperar_elemento("Descargar PDF")
    verificar_origen()


def esperar_y_click_aceptar(timeout=15):
    try:
        posicion = esperar_elemento("Aceptar", timeout)
    except TimeoutError:
        return False
    click_en(posicion, "Aceptar")
    return True


def descargar_pdf(cufe, nit):
    exigir_foco()
    verificar_origen()
    snapshot = snapshot_pdfs(DOWNLOADS_DIR)
    click_en(esperar_elemento("Descargar PDF"), "Descargar PDF")
    # Vigilar archivo y modal juntos: no asumir que el modal aparece o desaparece
    # después de una cantidad fija de segundos.
    fin = time.monotonic() + TIEMPO_MAX_DESCARGA
    aceptado = False
    while time.monotonic() < fin:
        pyautogui.failSafeCheck()
        try:
            origen = esperar_pdf(DOWNLOADS_DIR, snapshot, cufe, nit, timeout=1.2)
            return copiar_pdf(origen, cufe, nit)
        except TimeoutError:
            pass
        if not aceptado:
            palabras = leer_pantalla()
            comprobar_mensajes(palabras)
            aceptar = ubicar_texto(palabras, "Aceptar", exacto=True)
            if aceptar:
                click_en(aceptar, "Aceptar del modal de descarga")
                aceptado = True
    raise TimeoutError(f"No se descargó el PDF del CUFE en {TIEMPO_MAX_DESCARGA}s; modal aceptado: {aceptado}.")


def copiar_pdf(origen, cufe, nit):
    validar_pdf(origen, cufe, nit)
    destino = PDF_OUTPUT_DIR / f"{cufe}.pdf"
    if Path(origen).resolve() != destino.resolve():
        temporal = destino.with_suffix(".pdf.tmp")
        shutil.copy2(origen, temporal)
        validar_pdf(temporal, cufe, nit)
        temporal.replace(destino)
    return destino


def cerrar_ventana_pdf_por_cufe(cufe, timeout=3):
    fin = time.monotonic() + timeout
    while time.monotonic() < fin:
        ventanas = [w for w in gw.getAllWindows()
                    if cufe.lower() in (w.title or "").lower() and ".pdf" in w.title.lower()]
        if not ventanas:
            time.sleep(.3)
            continue
        for ventana in ventanas:
            if es_firefox(ventana):
                if not activar_ventana(ventana):
                    return False
                activa = gw.getActiveWindow()
                if obtener_hwnd(activa) != obtener_hwnd(ventana) or cufe.lower() not in activa.title.lower():
                    return False
                pyautogui.hotkey("ctrl", "w")
            else:
                ventana.close()
        time.sleep(.5)
        if any(cufe.lower() in (w.title or "").lower() and ".pdf" in w.title.lower()
               for w in gw.getAllWindows()):
            return False
        return True
    # No abrir visor es una configuración válida; no equivale a fallar la descarga.
    return True


def nuevo_resultado(registro):
    resultado = {campo: "" for campo in COLUMNAS_LOG}
    resultado.update({campo: registro[campo] for campo in ("id", "cufe", "nit_consulta")})
    resultado.update(estado_rpa="NO_PROCESADO", estado_extraccion="NO_PROCESADO", productos=0, intentos=0)
    return resultado


def procesar_registro(registro, indice, total, *, solo_extraccion=False, reusar_pdf=False,
                      ocr="auto", intentos=MAX_INTENTOS, salida="consolidado"):
    inicio = time.monotonic()
    resultado = nuevo_resultado(registro)
    print(f"\nPROCESANDO {indice}/{total}: ID {registro['id']}", flush=True)
    pdf = None
    cufe, nit = registro["cufe"], registro["nit_consulta"]
    try:
        if solo_extraccion or reusar_pdf:
            resultado["etapa"] = "RECUPERACION_PDF"
            try:
                origen = recuperar_pdf(PDF_OUTPUT_DIR, DOWNLOADS_DIR, cufe, nit)
                pdf = copiar_pdf(origen, cufe, nit)
                resultado["origen_pdf"] = str(origen)
                resultado["estado_rpa"] = "PDF_RECUPERADO"
            except FileNotFoundError:
                if solo_extraccion:
                    raise
        if pdf is None:
            for intento in range(1, intentos + 1):
                resultado["intentos"] = intento
                try:
                    resultado["etapa"] = "FORMULARIO"
                    navegar_a_busqueda_misma_ventana()
                    resultado["etapa"] = "CONSULTA"
                    consultar_factura(cufe, nit)
                    resultado["etapa"] = "DESCARGA"
                    pdf = descargar_pdf(cufe, nit)
                    resultado["estado_rpa"] = "PDF_DESCARGADO"
                    resultado["error"] = ""
                    break
                except (pyautogui.FailSafeException, KeyboardInterrupt):
                    raise
                except Exception as error:
                    resultado["error"] = f"{type(error).__name__}: {error}"
                    captura = capturar_error(cufe, resultado["etapa"])
                    print(f"Intento {intento}: {resultado['error']}; captura: {captura}", flush=True)
                    if isinstance(error, IntervencionHumanaPendiente):
                        raise
                    if isinstance(error, ValidacionSeguridadPendiente) and intento < intentos:
                        resultado["advertencia"] = resultado["error"]
                        print("Error de seguridad: recargando la consulta y reprocesando "
                              f"el mismo registro (intento {intento + 1}/{intentos}).", flush=True)
                        time.sleep(3)
                        # El siguiente intento abre nuevamente SearchDocument y diligencia
                        # el mismo CUFE/NIT antes de volver a solicitar el PDF.
                        continue
                    # Una descarga tardía o un modal ya cerrado no deben perder un PDF válido.
                    if resultado["etapa"] == "DESCARGA":
                        try:
                            origen = recuperar_pdf(PDF_OUTPUT_DIR, DOWNLOADS_DIR, cufe, nit)
                            pdf = copiar_pdf(origen, cufe, nit)
                            resultado["origen_pdf"] = str(origen)
                            resultado["estado_rpa"] = "PDF_RECUPERADO"
                            resultado["advertencia"] = resultado["error"]
                            resultado["error"] = ""
                            break
                        except FileNotFoundError:
                            pass
                    if intento == intentos:
                        raise
        resultado["pdf_path"] = str(pdf)
        resultado["etapa"] = "EXTRACCION"
        try:
            dataframe, csv_path = procesar_pdf(pdf, password=nit, ocr=ocr, salida=salida)
            resultado.update(estado_extraccion="CSV_EXPORTADO", csv_path=str(csv_path),
                             productos=len(dataframe), metodo_extraccion=dataframe.attrs.get("metodo_extraccion", ""))
        except Exception as error:
            resultado["estado_extraccion"] = "ERROR"
            resultado["error_extraccion"] = f"{type(error).__name__}: {error}"
        # La extracción y su resultado se conservan aunque falle el cierre del visor.
        if not solo_extraccion and resultado["intentos"]:
            try:
                if not cerrar_ventana_pdf_por_cufe(cufe):
                    resultado["advertencia"] += " Visor PDF no cerrado; se intentará recuperar DIAN."
            except (pyautogui.FailSafeException, KeyboardInterrupt):
                raise
            except Exception as error:
                resultado["advertencia"] += f" Cierre del visor: {error}"
        resultado["etapa"] = "FINALIZADO"
    except (pyautogui.FailSafeException, KeyboardInterrupt):
        resultado["estado_rpa"] = "INTERRUMPIDO"
        resultado["error"] = "Detenido por el usuario; no se enviarán más clics."
    except IntervencionHumanaPendiente as error:
        resultado["estado_rpa"] = "REQUIERE_INTERVENCION"
        resultado["error"] = str(error)
    except Exception as error:
        resultado["estado_rpa"] = "ERROR"
        resultado["error"] = f"{type(error).__name__}: {error}"
    finally:
        resultado["duracion_segundos"] = round(time.monotonic() - inicio, 2)
        resultado["fecha_proceso"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{resultado['estado_rpa']} | {resultado['estado_extraccion']} | {resultado['productos']} productos", flush=True)
    if resultado["error"] or resultado["error_extraccion"]:
        print(resultado["error"] or resultado["error_extraccion"], flush=True)
    return resultado


def verificar_cobertura(registros, resultados, salida="consolidado"):
    destino = CSV_OUTPUT_DIR / NOMBRE_CONSOLIDADO
    cantidades = {}
    if salida == "consolidado" and destino.exists():
        dataframe = pd.read_csv(destino, sep=";", dtype=str, keep_default_na=False)
        cantidades = dataframe.groupby("cufe").size().to_dict()
    detalles = []
    por_cufe = {r["cufe"]: r for r in resultados}
    for registro in registros:
        resultado = por_cufe.get(registro["cufe"], {})
        if salida == "individual":
            individual = CSV_OUTPUT_DIR / "individuales" / (registro["cufe"] + ".csv")
            if individual.exists():
                dataframe = pd.read_csv(individual, sep=";", dtype=str, keep_default_na=False)
                cantidades[registro["cufe"]] = int((dataframe["cufe"] == registro["cufe"]).sum())
        filas = int(cantidades.get(registro["cufe"], 0))
        exito = (resultado.get("estado_rpa") in {"PDF_DESCARGADO", "PDF_RECUPERADO"}
                 and resultado.get("estado_extraccion") == "CSV_EXPORTADO"
                 and filas == resultado.get("productos") and filas > 0)
        detalles.append({"id": registro["id"], "cufe": registro["cufe"],
                         "productos_consolidados": filas, "exito_ejecucion": exito,
                         "estado_rpa": resultado.get("estado_rpa", "NO_PROCESADO")})
    informe = {"salida": salida, "facturas_esperadas": len(registros), "facturas_exportadas": sum(d["exito_ejecucion"] for d in detalles),
               "productos_consolidados_del_input": sum(d["productos_consolidados"] for d in detalles),
               "consultas_con_descarga_nueva": sum(r["estado_rpa"] == "PDF_DESCARGADO" for r in resultados),
               "pdf_recuperados": sum(r["estado_rpa"] == "PDF_RECUPERADO" for r in resultados),
               "completo": all(d["exito_ejecucion"] for d in detalles), "detalle": detalles}
    ruta = LOG_DIR / "cobertura_rpa.json"
    ruta.write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nCobertura: {informe['facturas_exportadas']}/{len(registros)} facturas; "
          f"{informe['productos_consolidados_del_input']} productos presentes en CSV. Informe: {ruta}")
    return informe


def generar_reporte_metricas(informe, resultados, duracion, solo_extraccion=False):
    """Tabla de entrega calculada exclusivamente para la ejecución actual."""
    esperadas = informe["facturas_esperadas"]
    exportadas = informe["facturas_exportadas"]
    procesados = [r for r in resultados if r["estado_rpa"] != "NO_PROCESADO"]
    exitosos = {d["cufe"] for d in informe["detalle"] if d["exito_ejecucion"]}
    productos = sum(int(r["productos"]) for r in resultados if r["cufe"] in exitosos)
    def decimal(valor):
        return f"{valor:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    filas = [
        ("Facturas esperadas", esperadas), ("Facturas procesadas", len(procesados)),
        ("Facturas exportadas", exportadas), ("Productos extraídos", productos),
        ("Errores de extracción", sum(r["estado_extraccion"] == "ERROR" for r in resultados)),
        ("Tasa de éxito", f"{decimal(100 * exportadas / esperadas)} %" if esperadas else "No aplica"),
        ("Promedio productos/factura", decimal(productos / exportadas) if exportadas else "No aplica"),
        ("Tiempo total", f"{decimal(duracion)} s"),
        ("Tiempo promedio/factura", f"{decimal(sum(float(r['duracion_segundos'] or 0) for r in procesados) / len(procesados))} s"
         if procesados else "No aplica"),
    ]
    texto = (f"# Reporte de métricas\n\nFecha: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
             f"Modo: {'Solo extracción' if solo_extraccion else 'Flujo completo'}. "
             f"Salida: {informe.get('salida', 'consolidado')}.\n\n"
             "| Indicador | Resultado |\n|---|---:|\n" +
             "".join(f"| {nombre} | {valor} |\n" for nombre, valor in filas) +
             "\nEsperadas: registros válidos únicos seleccionados. Procesadas: registros iniciados, "
             "incluidos fallidos. Tasa de éxito: exportadas verificadas / esperadas. Productos y "
             "promedio de productos: solo exportaciones verificadas de esta ejecución.\n\n"
             "Tiempo total: desde el inicio del comando hasta verificar la cobertura, incluida "
             "la preparación. Promedio por factura: tiempos de los registros iniciados, incluidos "
             "reintentos, dividido por procesadas; excluye preparación general.\n")
    ruta = LOG_DIR / "reporte_metricas.md"
    ruta.write_text(texto, encoding="utf-8")
    print(f"Reporte de métricas: {ruta}")
    return ruta


def main():
    inicio_lote = time.monotonic()
    global DOWNLOADS_DIR, LOG_DIR, RESULTADOS_RPA_CSV
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cufes", type=int, default=MAX_CUFES)
    parser.add_argument("--solo-extraccion", action="store_true", help="Recuperar PDF del proyecto/Downloads sin navegador")
    parser.add_argument("--reusar-pdf", action="store_true", help="Consultar solo si no existe un PDF válido")
    parser.add_argument("--ocr", choices=["auto", "siempre", "nunca"], default="auto")
    parser.add_argument("--salida", choices=["consolidado", "individual"], default="consolidado")
    parser.add_argument("--intentos", type=int, choices=range(1, 4), default=MAX_INTENTOS)
    parser.add_argument("--descargas-dir", type=Path, default=DOWNLOADS_DIR)
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR, help="Separar el log de una prueba")
    args = parser.parse_args()
    DOWNLOADS_DIR = args.descargas_dir
    LOG_DIR = args.log_dir
    RESULTADOS_RPA_CSV = LOG_DIR / "resultados_rpa.csv"
    crear_carpetas()
    alertas = []
    registros = leer_entrada(INPUT_CSV, args.max_cufes, alertas=alertas)
    ruta_validacion = LOG_DIR / "validacion_input.csv"
    with ruta_validacion.open("w", newline="", encoding="utf-8-sig") as archivo:
        writer = csv.DictWriter(archivo, fieldnames=["fila", "id", "cufe_original", "cufe", "tipo", "mensaje"], delimiter=";")
        writer.writeheader()
        writer.writerows(alertas)
    for alerta in alertas:
        print(f"ALERTA INPUT fila {alerta['fila']} ({alerta['tipo']}): {alerta['mensaje']}", flush=True)
    if not registros:
        informe = verificar_cobertura([], [], salida=args.salida)
        generar_reporte_metricas(informe, [], time.monotonic() - inicio_lote, args.solo_extraccion)
        print(f"No hay registros PENDIENTES válidos. Validación: {ruta_validacion}")
        return 1 if any(a["tipo"] != "CUFE_NORMALIZADO" for a in alertas) else 0
    validar_salidas(args.salida, registros)
    if not args.solo_extraccion and not DOWNLOADS_DIR.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de descargas: {DOWNLOADS_DIR}")
    if RESULTADOS_RPA_CSV.exists():
        respaldo = LOG_DIR / f"resultados_rpa_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
        shutil.copy2(RESULTADOS_RPA_CSV, respaldo)
    resultados = []
    try:
        if not args.solo_extraccion:
            print("No uses mouse ni teclado. Para detener: esquina de pantalla o Ctrl+C.", flush=True)
            abrir_firefox()
        for indice, registro in enumerate(registros, start=1):
            resultado = procesar_registro(registro, indice, len(registros),
                solo_extraccion=args.solo_extraccion, reusar_pdf=args.reusar_pdf,
                ocr=args.ocr, intentos=args.intentos, salida=args.salida)
            resultados.append(resultado)
            guardar_resultados(resultados)
            if resultado["estado_rpa"] in {"INTERRUMPIDO", "REQUIERE_INTERVENCION"}:
                break
    except (pyautogui.FailSafeException, KeyboardInterrupt):
        print("Ejecución interrumpida antes de completar el lote.")
    except Exception as error:
        print(f"Error general: {type(error).__name__}: {error}", flush=True)
        if not args.solo_extraccion:
            capturar_error("inicio", "ERROR_GENERAL")
    finally:
        for registro in registros[len(resultados):]:
            pendiente = nuevo_resultado(registro)
            pendiente["error"] = "No ejecutado: el lote se detuvo antes de este registro."
            resultados.append(pendiente)
        guardar_resultados(resultados)
        informe = verificar_cobertura(registros, resultados, salida=args.salida)
        generar_reporte_metricas(informe, resultados, time.monotonic() - inicio_lote, args.solo_extraccion)
    return 0 if informe["completo"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
