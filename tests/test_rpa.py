"""Pruebas de los fallos que causaban pérdida o asociación incorrecta de PDF."""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, mock_open

import pandas as pd
import pyautogui

import rpa_archivos as archivos
import rpa_dian_batch as rpa
import procesamiento_pdf as procesamiento

BASE = Path(__file__).resolve().parents[1]


class PruebasRPA(unittest.TestCase):
    def test_metricas_no_cuentan_productos_historicos_ni_pendientes(self):
        informe = {"facturas_esperadas": 3, "facturas_exportadas": 1,
                   "detalle": [{"cufe": "a", "exito_ejecucion": True},
                               {"cufe": "b", "exito_ejecucion": False}]}
        resultados = [dict(cufe="a", estado_rpa="PDF_DESCARGADO", estado_extraccion="CSV_EXPORTADO", productos=2, duracion_segundos=4),
                      dict(cufe="b", estado_rpa="ERROR", estado_extraccion="ERROR", productos=10, duracion_segundos=2),
                      dict(cufe="c", estado_rpa="NO_PROCESADO", estado_extraccion="NO_PROCESADO", productos=0, duracion_segundos="")]
        with patch.object(Path, "write_text") as escribir:
            rpa.generar_reporte_metricas(informe, resultados, 9)
        texto = escribir.call_args.args[0]
        for fila in ("| Facturas procesadas | 2 |", "| Productos extraídos | 2 |",
                     "| Tasa de éxito | 33,33 % |", "| Tiempo promedio/factura | 3 s |"):
            self.assertIn(fila, texto)
        with patch.object(Path, "write_text") as escribir:
            rpa.generar_reporte_metricas(dict(facturas_esperadas=0, facturas_exportadas=0, detalle=[]), [], 1)
        self.assertIn("No aplica", escribir.call_args.args[0])

    def test_input_omite_invalidos_y_duplicados_normalizados(self):
        cufe = "a" * 96
        contenido = ("id;cufe;nit_consulta;estado_input\n"
                     f"1; {cufe.upper()} ;9001;PENDIENTE\n"
                     f"2;{cufe[:48]}-{cufe[48:]};9002;PENDIENTE\n"
                     "3;xyz;9001;PENDIENTE\n"
                     f"4;{'b' * 96};9001;PENDIENTE\n")
        alertas = []
        with patch.object(Path, "read_text", return_value=contenido):
            registros = archivos.leer_entrada("input.csv", limite=1, alertas=alertas)
        self.assertEqual([r["id"] for r in registros], ["1"])
        self.assertEqual(registros[0]["cufe"], cufe)
        self.assertEqual([a["tipo"] for a in alertas],
                         ["CUFE_NORMALIZADO", "CUFE_DUPLICADO", "INPUT_INVALIDO"])
        self.assertEqual(alertas[1]["fila"], 3)
        self.assertEqual(alertas[0]["cufe_original"], f" {cufe.upper()} ")

    def test_formato_cufe_no_corrige_caracteres_no_hexadecimales(self):
        for valor in ("", "a" * 95, "a" * 97, "g" * 96, "a" * 48 + " " + "a" * 48):
            self.assertFalse(archivos.validar_formato_cufe(valor)[1])
        self.assertTrue(archivos.validar_formato_cufe("A" * 48 + "-" + "A" * 48)[1])

    def test_exportacion_individual_reemplaza_solo_su_factura(self):
        with tempfile.TemporaryDirectory(dir=BASE) as carpeta:
            destino = Path(carpeta)
            consolidado = destino / procesamiento.NOMBRE_CONSOLIDADO
            consolidado.write_text("consolidado previo", encoding="utf-8")
            df = pd.DataFrame([{columna: "001" for columna in procesamiento.COLUMNAS}])
            with patch.object(procesamiento, "extraer_dataframe", return_value=df):
                _, ruta = procesamiento.procesar_pdf("factura_a.pdf", output_dir=destino, salida="individual")
            otra = ruta.parent / "factura_b.csv"
            otra.write_text("otra factura", encoding="utf-8")
            df = pd.DataFrame([{columna: "002" for columna in procesamiento.COLUMNAS}])
            with patch.object(procesamiento, "extraer_dataframe", return_value=df):
                procesamiento.procesar_pdf("factura_a.pdf", output_dir=destino, salida="individual")
            exportado = pd.read_csv(ruta, sep=";", dtype=str)
            self.assertEqual(len(exportado), 1)
            self.assertEqual(exportado.iloc[0]["codigo"], "002")
            self.assertEqual(exportado.iloc[0]["cufe"], "factura_a")
            self.assertEqual(otra.read_text(encoding="utf-8"), "otra factura")
            self.assertEqual(consolidado.read_text(encoding="utf-8"), "consolidado previo")

    def setUp(self):
        self.registro = {"id": "1", "cufe": "e" * 96, "nit_consulta": "900156264", "estado_input": "PENDIENTE"}
        self.pdf = BASE / "pdf_ficticios" / (self.registro["cufe"] + ".pdf")
        self.csv = '"id,cufe,nit_consulta,estado_input"\n' + '\n'.join(
            f'"{i},{i:096x},900156264,PENDIENTE"' for i in range(1, 11))

    def test_csv_legacy_no_pierde_registros(self):
        with patch.object(Path, "read_text", return_value=self.csv):
            registros = archivos.leer_entrada("input.csv")
        self.assertEqual(len(registros), 10)
        self.assertEqual(len({r["cufe"] for r in registros}), 10)

    def test_limite_invalido(self):
        with patch.object(Path, "read_text", return_value=self.csv), self.assertRaises(ValueError):
            archivos.leer_entrada("input.csv", 0)

    def test_rechaza_pdf_de_otro_cufe(self):
        pdf = Mock()
        pdf.pages = [Mock(extract_text=Mock(return_value=self.registro["cufe"]))]
        lector = Mock()
        lector.__enter__ = Mock(return_value=pdf)
        lector.__exit__ = Mock(return_value=False)
        with patch.object(Path, "open", mock_open(read_data=b"%PDF-")), \
             patch.object(archivos.pdfplumber, "open", return_value=lector), \
             self.assertRaisesRegex(ValueError, "CUFE"):
            archivos.validar_pdf(self.pdf, "0" * 96, self.registro["nit_consulta"])

    def test_no_selecciona_el_pdf_mas_reciente_de_otro_cufe(self):
        with patch.object(Path, "glob", return_value=[self.pdf, BASE / ("0" * 96 + ".pdf")]):
            candidatos = archivos.candidatos_pdf(BASE, self.registro["cufe"])
        self.assertEqual(candidatos, [self.pdf])

    def test_descarga_exige_estabilidad_sin_esperar_parts_ajenos(self):
        candidato = Mock()
        candidato.stat.side_effect = [Mock(st_mtime_ns=1, st_size=10),
                                     Mock(st_mtime_ns=2, st_size=20),
                                     Mock(st_mtime_ns=2, st_size=20)]
        candidato.with_suffix.return_value.exists.return_value = False
        candidato.suffix = ".pdf"
        with patch.object(archivos, "candidatos_pdf", return_value=[candidato]), \
             patch.object(archivos, "validar_pdf", return_value=candidato) as validar, \
             patch.object(archivos.time, "sleep"):
            obtenido = archivos.esperar_pdf(BASE, {}, self.registro["cufe"], "123", timeout=1)
        self.assertIs(obtenido, candidato)
        self.assertEqual(candidato.stat.call_count, 3)
        validar.assert_called_once()

    def test_pdf_antiguo_no_pasa_como_descarga_nueva(self):
        candidato = Mock(suffix=".pdf")
        candidato.stat.return_value = Mock(st_mtime_ns=1, st_size=10)
        with patch.object(archivos, "candidatos_pdf", return_value=[candidato]), \
             patch.object(archivos, "validar_pdf") as validar:
            with self.assertRaises(TimeoutError):
                archivos.esperar_pdf(BASE, {candidato: (1, 10)}, self.registro["cufe"], "123", timeout=.01)
        validar.assert_not_called()

    def test_editor_con_dian_en_titulo_no_es_firefox(self):
        self.assertFalse(rpa.es_firefox(Mock(title="rpa_dian_batch.py - Visual Studio Code")))
        self.assertFalse(rpa.es_firefox(Mock(title="flujo_dian_firefox_rpa.py - Visual Studio Code")))
        self.assertTrue(rpa.es_firefox(Mock(title="DIAN | Acceder — Mozilla Firefox")))

    def test_buscar_no_confunde_titulo_con_boton(self):
        def palabra(texto, y, linea):
            return {"text": texto, "left": "10", "top": str(y), "width": "60", "height": "20",
                    "block_num": "1", "par_num": "1", "line_num": str(linea)}
        palabras = [palabra("Buscar", 100, 1), palabra("documento", 100, 1), palabra("Buscar", 200, 2)]
        self.assertEqual(rpa.ubicar_texto(palabras, "Buscar", exacto=True)["y"], 210)
        self.assertIsNone(rpa.ubicar_texto(palabras[:2], "Buscar", exacto=True))

    def test_ocr_conserva_letras_blancas_del_boton_aceptar(self):
        from PIL import Image, ImageDraw, ImageFont
        imagen = Image.new("RGB", (320, 120), "white")
        dibujo = ImageDraw.Draw(imagen)
        dibujo.rounded_rectangle((60, 30, 240, 80), radius=4, fill=(44, 190, 125))
        fuente = ImageFont.truetype("arialbd.ttf", 22)
        dibujo.text((100, 42), "Aceptar", font=fuente, fill="white")
        palabras = rpa.reconocer_pantalla(imagen)
        posicion = rpa.ubicar_texto(palabras, "Aceptar", exacto=True)
        self.assertIsNotNone(posicion)
        self.assertTrue(60 <= posicion["x"] <= 240)
        self.assertTrue(30 <= posicion["y"] <= 80)

    def test_elemento_ausente_no_produce_clic(self):
        with patch.object(rpa, "leer_pantalla", return_value=[]), \
             patch.object(rpa.time, "monotonic", side_effect=[0, 0, 2]), \
             patch.object(rpa.time, "sleep"), patch.object(rpa.pyautogui, "click") as click:
            with self.assertRaises(TimeoutError):
                rpa.esperar_elemento("Aceptar", timeout=1)
        click.assert_not_called()

    def test_cerrar_visor_no_borra_extraccion_exitosa(self):
        df = pd.DataFrame([{"codigo": "0"}])
        df.attrs["metodo_extraccion"] = "TEXTO"
        with patch.object(rpa, "navegar_a_busqueda_misma_ventana"), \
             patch.object(rpa, "consultar_factura"), \
             patch.object(rpa, "descargar_pdf", return_value=self.pdf), \
             patch.object(rpa, "procesar_pdf", return_value=(df, BASE / "data/csv/facturas_consolidadas.csv")), \
             patch.object(rpa, "cerrar_ventana_pdf_por_cufe", side_effect=RuntimeError("visor bloqueado")):
            resultado = rpa.procesar_registro(self.registro, 1, 1)
        self.assertEqual(resultado["estado_rpa"], "PDF_DESCARGADO")
        self.assertEqual(resultado["estado_extraccion"], "CSV_EXPORTADO")
        self.assertEqual(resultado["productos"], 1)
        self.assertIn("visor bloqueado", resultado["advertencia"])

    def test_failsafe_detiene_sin_reintento(self):
        with patch.object(rpa, "navegar_a_busqueda_misma_ventana", side_effect=pyautogui.FailSafeException), \
             patch.object(rpa, "consultar_factura") as consultar, \
             patch.object(rpa, "procesar_pdf") as extraer:
            resultado = rpa.procesar_registro(self.registro, 1, 1, intentos=2)
        self.assertEqual(resultado["estado_rpa"], "INTERRUMPIDO")
        self.assertEqual(resultado["intentos"], 1)
        consultar.assert_not_called()
        extraer.assert_not_called()

    def test_error_extraccion_conserva_ruta_pdf(self):
        with patch.object(rpa, "recuperar_pdf", return_value=self.pdf), \
             patch.object(rpa, "copiar_pdf", return_value=self.pdf), \
             patch.object(rpa, "procesar_pdf", side_effect=PermissionError("CSV abierto en Excel")):
            resultado = rpa.procesar_registro(self.registro, 1, 1, solo_extraccion=True)
        self.assertEqual(resultado["estado_rpa"], "PDF_RECUPERADO")
        self.assertEqual(resultado["pdf_path"], str(self.pdf))
        self.assertEqual(resultado["estado_extraccion"], "ERROR")
        self.assertIn("Excel", resultado["error_extraccion"])

    def test_descarga_con_modal_acepta_antes_de_recibir_pdf(self):
        aceptar = {"text": "Aceptar", "left": "10", "top": "200", "width": "60", "height": "20",
                   "block_num": "1", "par_num": "1", "line_num": "1"}
        with patch.object(rpa, "exigir_foco"), patch.object(rpa, "verificar_origen"), \
             patch.object(rpa, "snapshot_pdfs", return_value={}), \
             patch.object(rpa, "esperar_elemento", return_value={"x": 100, "y": 100}), \
             patch.object(rpa, "click_en") as click, patch.object(rpa.pyautogui, "failSafeCheck"), \
             patch.object(rpa, "esperar_pdf", side_effect=[TimeoutError(), self.pdf]), \
             patch.object(rpa, "leer_pantalla", return_value=[aceptar]), \
             patch.object(rpa, "copiar_pdf", return_value=self.pdf):
            obtenido = rpa.descargar_pdf(self.registro["cufe"], self.registro["nit_consulta"])
        self.assertEqual(obtenido, self.pdf)
        self.assertEqual(click.call_count, 2)
        self.assertIn("Aceptar", click.call_args.args[1])

    def test_descarga_sin_modal_no_exige_aceptar(self):
        with patch.object(rpa, "exigir_foco"), patch.object(rpa, "verificar_origen"), \
             patch.object(rpa, "snapshot_pdfs", return_value={}), \
             patch.object(rpa, "esperar_elemento", return_value={"x": 100, "y": 100}), \
             patch.object(rpa, "click_en"), patch.object(rpa.pyautogui, "failSafeCheck"), \
             patch.object(rpa, "esperar_pdf", return_value=self.pdf), \
             patch.object(rpa, "leer_pantalla") as leer, \
             patch.object(rpa, "copiar_pdf", return_value=self.pdf):
            obtenido = rpa.descargar_pdf(self.registro["cufe"], self.registro["nit_consulta"])
        self.assertEqual(obtenido, self.pdf)
        leer.assert_not_called()

    def test_cobertura_no_confunde_filas_historicas_con_exito_actual(self):
        df = pd.DataFrame([{"cufe": self.registro["cufe"]}])
        resultado = rpa.nuevo_resultado(self.registro)
        resultado["estado_rpa"] = "ERROR"
        with patch.object(Path, "exists", return_value=True), \
             patch.object(rpa.pd, "read_csv", return_value=df), \
             patch.object(Path, "write_text"):
            informe = rpa.verificar_cobertura([self.registro], [resultado])
        self.assertFalse(informe["completo"])
        self.assertEqual(informe["facturas_exportadas"], 0)
        self.assertEqual(informe["productos_consolidados_del_input"], 1)

    def test_aceptar_alerta_de_seguridad_no_es_confirmacion_de_descarga(self):
        palabras = [{"text": t} for t in "El campo de seguridad no está completo Aceptar".split()]
        with self.assertRaises(rpa.ValidacionSeguridadPendiente):
            rpa.comprobar_mensajes(palabras)

    def test_error_seguridad_reconsulta_antes_de_recuperar_pdf(self):
        df = pd.DataFrame([{"codigo": "0"}])
        with patch.object(rpa, "navegar_a_busqueda_misma_ventana") as navegar, \
             patch.object(rpa, "consultar_factura") as consultar, \
             patch.object(rpa, "descargar_pdf", side_effect=[
                 rpa.ValidacionSeguridadPendiente("Falta token de validacion de captcha"), self.pdf]), \
             patch.object(rpa, "recuperar_pdf", return_value=self.pdf) as recuperar, \
             patch.object(rpa, "capturar_error", return_value=""), \
             patch.object(rpa.time, "sleep"), \
             patch.object(rpa, "procesar_pdf", return_value=(df, BASE / "consolidado.csv")), \
             patch.object(rpa, "cerrar_ventana_pdf_por_cufe", return_value=True):
            resultado = rpa.procesar_registro(self.registro, 1, 1, intentos=2)
        self.assertEqual(navegar.call_count, 2)
        self.assertEqual(consultar.call_count, 2)
        for llamada in consultar.call_args_list:
            self.assertEqual(llamada.args, (self.registro["cufe"], self.registro["nit_consulta"]))
        recuperar.assert_not_called()
        self.assertEqual(resultado["estado_rpa"], "PDF_DESCARGADO")
        self.assertEqual(resultado["estado_extraccion"], "CSV_EXPORTADO")
        self.assertEqual(resultado["intentos"], 2)
        self.assertEqual(resultado["error"], "")

    def test_error_seguridad_agota_intentos_sin_reintentar_indefinidamente(self):
        with patch.object(rpa, "navegar_a_busqueda_misma_ventana") as navegar, \
             patch.object(rpa, "consultar_factura"), \
             patch.object(rpa, "descargar_pdf", side_effect=rpa.ValidacionSeguridadPendiente("Seguridad incompleta")), \
             patch.object(rpa, "recuperar_pdf", side_effect=FileNotFoundError) as recuperar, \
             patch.object(rpa, "capturar_error", return_value=""), \
             patch.object(rpa.time, "sleep"), \
             patch.object(rpa, "procesar_pdf") as extraer:
            resultado = rpa.procesar_registro(self.registro, 1, 1, intentos=2)
        self.assertEqual(navegar.call_count, 2)
        recuperar.assert_called_once()
        extraer.assert_not_called()
        self.assertEqual(resultado["estado_rpa"], "ERROR")
        self.assertEqual(resultado["intentos"], 2)

    def test_captcha_humano_no_se_reintenta(self):
        with patch.object(rpa, "navegar_a_busqueda_misma_ventana",
                          side_effect=rpa.IntervencionHumanaPendiente("Completar verificación humana")) as navegar, \
             patch.object(rpa, "capturar_error", return_value=""), \
             patch.object(rpa, "consultar_factura") as consultar:
            resultado = rpa.procesar_registro(self.registro, 1, 1, intentos=2)
        self.assertEqual(resultado["estado_rpa"], "REQUIERE_INTERVENCION")
        self.assertEqual(navegar.call_count, 1)
        consultar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
