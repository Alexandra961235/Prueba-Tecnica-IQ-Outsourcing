# Automatización DIAN y extracción de facturas electrónicas

Solución desarrollada en Python para automatizar la consulta de documentos electrónicos en el portal de la DIAN a partir de códigos CUFE, descargar la representación gráfica de cada factura en formato PDF y transformar su contenido en datos estructurados para análisis.

El proyecto cubre dos etapas principales:

1. **RPA DIAN:** consulta y descarga automatizada de los documentos.
2. **Procesamiento de facturas:** extracción, validación y exportación de la información contenida en los PDF.

---

## 1. Objetivo

Automatizar el flujo completo desde la consulta del documento electrónico hasta la generación de archivos CSV con la información estructurada de cada factura.

El proceso permite:

- validar los CUFE de entrada;
- consultar documentos en el portal de la DIAN;
- descargar la representación gráfica en PDF;
- procesar documentos con texto seleccionable;
- utilizar OCR como mecanismo alternativo;
- generar un DataFrame por factura;
- exportar resultados individuales o consolidados;
- validar la información extraída;
- registrar tiempos, errores, cobertura y métricas del proceso.

---

## 2. Flujo general

```text
data/input/cufes.csv
        │
        ▼
Validación del input
        │
        ▼
Consulta DIAN
        │
        ▼
Descarga PDF
        │
        ▼
data/pdf/<CUFE>.pdf
        │
        ▼
Extracción
   ┌────┴─────┐
   │          │
 TEXTO       OCR
   │          │
   └────┬─────┘
        ▼
Validación de datos
        │
        ▼
DataFrame por factura
        │
        ├── CSV individual
        │
        ▼
CSV consolidado
        │
        ▼
Logs, cobertura y métricas
```

---

## 3. Estructura del proyecto

```text
prueba-dian-rpa/
│
├── data/
│   ├── input/
│   │   └── cufes.csv
│   │
│   ├── pdf/
│   │   └── <CUFE>.pdf
│   │
│   └── csv/
│       ├── facturas_consolidadas.csv
│       └── individuales/
│           └── <CUFE>.csv
│
├── logs/
│   ├── resultados_rpa.csv
│   ├── validacion_input.csv
│   ├── cobertura_rpa.json
│   └── reporte_metricas.md
│
├── debug/
│
├── tools/
│   └── tessdata/
│       └── spa.traineddata
│
├── tests/
│
├── rpa_dian_batch.py
├── procesamiento_pdf.py
├── requirements-pdf.txt
└── README.md
```

---

## 4. Requisitos

### Software

El flujo requiere:

- Python 3;
- Firefox;
- Tesseract OCR para los escenarios que requieren reconocimiento óptico;
- entorno virtual de Python recomendado.

### Librerías principales

Entre las dependencias utilizadas se encuentran:

```text
pandas
pdfplumber
pyautogui
pygetwindow
pyperclip
```

Las dependencias asociadas al procesamiento de PDF se encuentran en:

```text
requirements-pdf.txt
```

Para OCR se utiliza:

```text
Tesseract
tools/tessdata/spa.traineddata
```

---

## 5. Archivo de entrada

El proceso toma como entrada:

```text
data/input/cufes.csv
```

Columnas esperadas:

```text
id
cufe
nit_consulta
estado_input
```

Ejemplo:

```csv
id;cufe;nit_consulta;estado_input
1;eccc372e4bdb8125...;900156264;PENDIENTE
2;8e7b821b46445807...;900156264;PENDIENTE
```

Solo se procesan registros cuyo `estado_input` sea:

```text
PENDIENTE
```

El campo `nit_consulta` se utiliza durante la consulta y, cuando corresponde, para abrir documentos PDF protegidos.

---

## 6. Validación del input

Antes de abrir Firefox se revisan todos los registros pendientes.

La validación:

- elimina espacios externos;
- elimina guiones del CUFE;
- convierte el CUFE a minúsculas;
- verifica que tenga exactamente 96 caracteres;
- verifica que contenga únicamente caracteres hexadecimales (`0-9`, `a-f`);
- detecta registros duplicados.

La validación comprueba únicamente el **formato del CUFE**. No garantiza que el documento exista en la DIAN.

No se corrigen automáticamente espacios internos ni caracteres inesperados.

Los CUFE inválidos y los duplicados se excluyen del procesamiento.

Cuando existen duplicados se conserva la primera fila válida pendiente correspondiente al CUFE normalizado.

Las incidencias se registran en:

```text
logs/validacion_input.csv
```

Este archivo contiene información como:

```text
fila
id
cufe_original
cufe_normalizado
tipo_alerta
motivo
```

Las alertas también se muestran en consola.

---

## 7. Ejecutar el flujo completo

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py
```

El flujo realiza las siguientes operaciones:

1. lee `data/input/cufes.csv`;
2. valida los registros de entrada;
3. abre Firefox;
4. accede al portal de consulta de documentos electrónicos;
5. diligencia el CUFE;
6. diligencia el NIT de consulta;
7. ejecuta la consulta;
8. accede al detalle del documento;
9. solicita la descarga del PDF;
10. valida el modal de descarga;
11. descarga el documento;
12. valida y copia el PDF a `data/pdf`;
13. cierra el visor correspondiente al PDF;
14. regresa al formulario de consulta;
15. recarga la página antes del siguiente documento;
16. extrae la información de la factura;
17. genera la salida seleccionada;
18. actualiza logs, cobertura y métricas.

Firefox se reutiliza durante el lote para evitar crear una sesión nueva por cada factura.

---

## 8. Ejecución de prueba

Para probar el proceso con una cantidad reducida de registros:

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --max-cufes 2 --log-dir logs/prueba_ui
```

Este modo permite validar el ciclo:

```text
consulta
→ descarga
→ cierre del visor
→ regreso
→ recarga
→ siguiente CUFE
```

sin modificar el código fuente.

---

## 9. Consideraciones durante la ejecución del RPA

El proceso utiliza automatización de escritorio.

Durante la ejecución se recomienda:

- no utilizar el mouse;
- no utilizar el teclado;
- mantener Firefox visible;
- no cambiar el tamaño de la ventana;
- no modificar la escala de pantalla;
- cerrar los CSV abiertos en Excel antes de comenzar.

Para detener el proceso se puede utilizar:

```text
Ctrl+C
```

o mover el cursor a una esquina de la pantalla para activar el mecanismo `FAILSAFE` de PyAutoGUI.

La interrupción manual no se convierte automáticamente en un reintento.

La carpeta de descargas configurada en Firefox debe corresponder con `Downloads` o indicarse mediante:

```text
--descargas-dir
```

---

## 10. Reintentos y recuperación

Por defecto se permiten hasta dos intentos por registro.

### Sin reintentos

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --intentos 1
```

### Hasta tres intentos

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --intentos 3
```

Los reintentos se utilizan para fallos recuperables como:

- página incompleta;
- error temporal de consulta;
- modal no identificado;
- descarga no detectada;
- estado inválido de la página;
- necesidad de recargar la consulta.

Antes de repetir una consulta se recarga la página y se diligencian nuevamente el CUFE y el NIT.

Los controles humanos o mecanismos anti-bot del portal no se intentan eludir automáticamente.

Si el portal requiere intervención humana, el proceso puede registrar:

```text
REQUIERE_INTERVENCION
```

y los registros pendientes pueden quedar como:

```text
NO_PROCESADO
```

Una vez completada manualmente la validación visible en Firefox, el lote puede ejecutarse nuevamente.

---

## 11. Modos de salida

El parámetro:

```text
--salida
```

está disponible en:

```text
rpa_dian_batch.py
procesamiento_pdf.py
```

Los modos soportados son:

```text
consolidado
individual
```

El valor predeterminado es:

```text
consolidado
```

### Salida consolidada

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --salida consolidado
```

Genera o actualiza:

```text
data/csv/facturas_consolidadas.csv
```

### Salida individual

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --salida individual
```

Genera un archivo por factura:

```text
data/csv/individuales/<CUFE>.csv
```

Cada CSV contiene una fila por producto.

Si una factura es reprocesada, su archivo individual es reemplazado.

En este modo el consolidado existente se conserva sin modificaciones.

---

## 12. Procesamiento de PDF ya descargados

Para ejecutar únicamente la fase de extracción:

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --solo-extraccion
```

Este modo:

- no abre Firefox;
- no utiliza mouse ni teclado;
- procesa los PDF disponibles;
- valida los documentos;
- extrae la información;
- exporta los resultados;
- genera métricas.

También puede ejecutarse directamente:

```powershell
.\.venv\Scripts\python.exe procesamiento_pdf.py
```

Para generar CSV individuales a partir de los PDF existentes:

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --solo-extraccion --salida individual
```

---

## 13. Reutilización de PDF existentes

La opción:

```text
--reusar-pdf
```

permite reutilizar documentos que ya se encuentran disponibles y consultar únicamente los faltantes.

El proceso puede buscar archivos asociados al CUFE en:

```text
data/pdf
```

y en la carpeta de descargas configurada.

Los documentos recuperados son validados antes de utilizarse.

La existencia de un PDF histórico no convierte automáticamente una consulta fallida en una ejecución exitosa.

---

## 14. Extracción de información

Cada PDF se procesa de manera independiente y genera un DataFrame asociado al documento.

De las secciones generales de la factura se extraen:

```text
numero_factura
fecha_emision
nit_emisor
```

Del detalle de productos se extraen:

```text
codigo
descripcion
cantidad
precio_unitario
```

Adicionalmente se conserva:

```text
cufe
```

como identificador de trazabilidad.

Cada fila representa un producto.

Por ejemplo, si una factura contiene seis productos, el DataFrame contiene seis filas y repite los datos generales de la factura en cada una.

---

## 15. Estructura del resultado

El consolidado se almacena en:

```text
data/csv/facturas_consolidadas.csv
```

Columnas:

```text
cufe
numero_factura
fecha_emision
nit_emisor
codigo
descripcion
cantidad
precio_unitario
```

Características:

- una fila por producto;
- separador `;`;
- codificación UTF-8 con BOM;
- NIT y código conservados como texto;
- cantidades y valores numéricos sin separadores de miles.

Las descripciones pueden contener saltos de línea dentro de campos CSV correctamente delimitados.

Por esta razón, para contabilizar los registros se recomienda utilizar Pandas o un lector CSV y no contar las líneas físicas del archivo.

---

## 16. OCR

El modo predeterminado:

```text
--ocr auto
```

prioriza la extracción de texto directamente desde el PDF.

Cuando una página no contiene texto utilizable, el proceso puede recurrir a OCR.

Para forzar OCR en todas las páginas:

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --solo-extraccion --ocr siempre
```

Se utiliza Tesseract con el modelo de idioma español:

```text
tools/tessdata/spa.traineddata
```

El ejecutable puede detectarse desde `PATH` o desde ubicaciones estándar de Windows.

También se pueden utilizar las variables:

```text
TESSERACT_CMD
OCR_LANG
```

para personalizar el motor y el idioma.

El procesamiento se realiza localmente.

### Métodos de extracción

Los logs identifican el método utilizado como:

```text
TEXTO
OCR
MIXTO
```

- `TEXTO`: extracción directa desde la capa textual del PDF.
- `OCR`: extracción mediante reconocimiento óptico.
- `MIXTO`: combinación de ambas estrategias.

---

## 17. Validaciones de extracción

Antes de exportar un documento se verifica:

- apertura correcta del PDF;
- existencia de texto u OCR utilizable;
- correspondencia del documento con el CUFE esperado;
- número de factura;
- fecha de emisión;
- NIT del emisor;
- existencia del detalle de productos;
- código;
- descripción;
- cantidad;
- precio unitario.

Los registros con errores no se descartan silenciosamente.

Cada incidencia queda registrada para permitir su diagnóstico.

Una factura no se considera correctamente exportada únicamente por haber sido descargada. También debe superar las validaciones de extracción y generar la salida correspondiente.

---

## 18. Métricas, tiempos y trazabilidad

Al finalizar cada lote se genera:

```text
logs/reporte_metricas.md
```

Este archivo contiene un resumen de indicadores del proceso y respeta la ubicación configurada mediante `--log-dir`.

El reporte identifica si la ejecución corresponde a:

```text
flujo completo
solo extracción
```

### Tiempos registrados

El proceso puede reportar:

- tiempo total del lote;
- duración por factura;
- tiempo promedio por factura procesada;
- tiempo mínimo;
- tiempo máximo;
- tiempos asociados a reintentos;
- tiempo de preparación;
- duración del flujo completo o de la extracción.

Los tiempos pueden variar entre ejecuciones debido a:

- latencia del portal DIAN;
- velocidad de descarga;
- rendimiento del equipo;
- cantidad de páginas;
- cantidad de productos;
- uso de OCR;
- cantidad de reintentos.

Por esta razón, los tiempos registrados representan las condiciones de la ejecución observada y no deben interpretarse como tiempos garantizados para otros lotes.

### Métricas de procesamiento

Entre los indicadores registrados se encuentran:

```text
facturas_esperadas
facturas_procesadas
facturas_exportadas
facturas_con_error
productos_extraidos
descargas_nuevas
pdf_recuperados
tasa_exito
tasa_error
promedio_productos_factura
```

Las facturas esperadas corresponden únicamente a los registros válidos, únicos y seleccionados del archivo de entrada.

Las exportaciones se verifican contra los CSV generados durante la ejecución.

La existencia de información histórica en los archivos de salida no convierte una ejecución actual fallida en exitosa.

### Indicadores derivados

```text
tasa_exito =
    facturas_exportadas / facturas_esperadas * 100

tasa_error =
    facturas_con_error / facturas_esperadas * 100

promedio_productos_factura =
    productos_extraidos / facturas_exportadas
```

Cuando el denominador es cero, el indicador se maneja de forma controlada para evitar divisiones inválidas.

### Métricas de calidad

También se controlan:

- presencia de todos los campos obligatorios;
- cantidad de productos extraídos;
- método de extracción;
- correspondencia CUFE-documento;
- advertencias;
- errores de validación;
- resultado de la extracción.

---

## 19. Logs de ejecución

### Resultado por factura

```text
logs/resultados_rpa.csv
```

Registra información como:

```text
CUFE
estado
intentos
ruta_pdf
resultado_extraccion
productos
metodo_extraccion
duracion
advertencias
error
```

Los logs anteriores se respaldan antes de iniciar un nuevo lote.

Los fallos de cierre del visor se registran independientemente y no invalidan automáticamente una extracción que haya sido completada correctamente.

### Validación del input

```text
logs/validacion_input.csv
```

Registra normalizaciones, CUFE inválidos, duplicados y otras incidencias detectadas antes de iniciar el RPA.

---

## 20. Cobertura del lote

El archivo:

```text
logs/cobertura_rpa.json
```

resume la ejecución.

Puede incluir métricas como:

```text
facturas_esperadas
facturas_procesadas
facturas_exportadas
productos
descargas_nuevas
pdf_recuperados
errores
```

La cobertura se calcula utilizando únicamente los registros válidos y únicos del input.

Un lote incompleto finaliza con código de salida:

```text
1
```

Esto permite identificar ejecuciones parciales desde scripts, pruebas o pipelines.

---

## 21. Resultado de la ejecución de validación

Con el conjunto de entrada actual se obtuvo:

| Indicador | Resultado |
|---|---:|
| Facturas válidas esperadas | 10 |
| Facturas exportadas | 10 |
| Productos extraídos | 21 |
| Errores de extracción | 0 |
| Tasa de éxito | 100 % |
| Promedio de productos por factura | 2.1 |

Los tiempos no se fijan directamente en este README porque pueden variar entre ejecuciones.

Los valores reales deben consultarse en:

```text
logs/reporte_metricas.md
```

Los detalles completos se encuentran en:

```text
logs/resultados_rpa.csv
logs/cobertura_rpa.json
logs/reporte_metricas.md
```

> Los resultados anteriores corresponden únicamente al conjunto de prueba actual. Un nuevo archivo `cufes.csv` puede producir cantidades, tiempos y métricas diferentes.

---

## 22. Restricciones conocidas

### Dependencia del portal DIAN

El RPA interactúa con un sistema externo.

El proceso puede verse afectado por:

- indisponibilidad temporal;
- latencia;
- cambios de interfaz;
- cambios en el flujo de navegación;
- cambios en mecanismos de seguridad o validación.

### Automatización visual

Parte de la interacción depende de automatización de escritorio.

Por esta razón, pueden influir:

- resolución de pantalla;
- escalado de Windows;
- tamaño y posición de Firefox;
- posición de controles;
- cambios en el diseño visual.

### Controles humanos y seguridad

Los controles humanos o mecanismos anti-bot no se intentan eludir automáticamente.

Si el portal solicita una validación humana, el proceso registra la condición y requiere intervención antes de continuar.

### Formato de las facturas

La extracción está adaptada a los formatos observados durante la prueba.

Cambios en:

- encabezados;
- nombres de columnas;
- estructura de tablas;
- posición de campos;
- distribución visual;

pueden requerir ajustes en las reglas de extracción.

### OCR

El OCR puede presentar errores en:

- caracteres similares como `0/O` o `1/I`;
- espacios;
- signos;
- números;
- tablas;
- documentos inclinados;
- imágenes de baja resolución.

Por esta razón, los resultados obtenidos mediante OCR requieren validaciones adicionales.

### Archivos protegidos

Algunos PDF pueden requerir contraseña.

El proceso utiliza el NIT asociado al CUFE cuando corresponde.

### Rendimiento

El tiempo total puede variar según:

- cantidad de facturas;
- respuesta del portal;
- número de páginas;
- cantidad de productos;
- velocidad de descarga;
- uso de OCR;
- cantidad de reintentos;
- capacidad del equipo.

Los tiempos reportados deben interpretarse como valores de referencia de cada ejecución.

---

## 23. Automatización visual

La interacción con el portal utiliza automatización de escritorio y mecanismos de reconocimiento visual.

El campo CUFE utiliza una posición calibrada mediante:

```text
POS_CAMPO_CUFE
```

Los campos diligenciados se comprueban antes de ejecutar la consulta.

Los elementos visuales relevantes, como botones y mensajes del portal, se identifican mediante los mecanismos implementados en el RPA.

Un cambio en:

- resolución;
- escalado;
- diseño del portal;
- tamaño de Firefox;
- posición de elementos;

puede requerir recalibración.

---

## 24. Pruebas automatizadas

Ejecutar:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las pruebas utilizan datos simulados y no dependen de los PDF de producción.

Cubren escenarios como:

- campos ausentes;
- elementos visuales no encontrados;
- estabilidad de las descargas;
- validación del CUFE;
- modal de descarga;
- parada de emergencia;
- visor bloqueado;
- CSV bloqueado;
- cobertura con filas históricas;
- recuperación de documentos;
- errores de extracción.

Las pruebas automatizadas complementan, pero no reemplazan, una ejecución real con Firefox y el escritorio disponible.

Las pruebas que dependen de OCR requieren Tesseract instalado.

---

## 25. Archivos temporales y depuración

Los siguientes elementos pueden eliminarse cuando ya no sean necesarios:

```text
data/csv-ocr
tmp
__pycache__
browser_profiles
posiciones_firefox.json
debug
logs de pruebas
```

No deben eliminarse mientras sean necesarios para reproducir o analizar una incidencia.

Se recomienda conservar como parte de la entrega:

```text
data/input/
data/pdf/
data/csv/
logs/
tools/tessdata/
tests/
rpa_dian_batch.py
procesamiento_pdf.py
requirements-pdf.txt
README.md
```

---

## 26. Resumen de comandos

### Ejecutar flujo completo

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py
```

### Procesar solo dos CUFE

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --max-cufes 2
```

### Procesar únicamente los PDF existentes

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --solo-extraccion
```

### Generar salida individual

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --solo-extraccion --salida individual
```

### Generar salida consolidada

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --solo-extraccion --salida consolidado
```

### Forzar OCR

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --solo-extraccion --ocr siempre
```

### Ejecutar con un solo intento

```powershell
.\.venv\Scripts\python.exe rpa_dian_batch.py --intentos 1
```

### Ejecutar pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

---

## 27. Resultado final

La solución permite ejecutar un flujo trazable de extremo a extremo:

```text
CUFE
  ↓
Validación
  ↓
Consulta DIAN
  ↓
PDF
  ↓
Extracción TEXTO / OCR
  ↓
Validación
  ↓
DataFrame
  ↓
CSV individual / consolidado
  ↓
Logs + cobertura + métricas
```

El diseño separa la automatización del portal de la extracción de información, lo que permite reprocesar documentos ya descargados sin necesidad de ejecutar nuevamente el RPA.