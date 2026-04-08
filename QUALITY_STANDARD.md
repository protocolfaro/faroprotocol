# FARO PROTOCOL — Estándar de Calidad de Outputs
> Versión 1.0 — 2026-04-07
> Este archivo es leído automáticamente por faro_certificado.py,
> faro_bricolage.py y faro_report_engine.py antes de generar cualquier output.

---

## AUDIENCIA

Los clientes de Faro Protocol son profesionales institucionales:
fondos de inversión, trading desks, gobiernos, directivos de empresas.

- **Miran cada detalle.** Un título duplicado, un recorte incorrecto
  o un score incorrecto destruye la credibilidad del sistema completo.
- **Auditan los datos.** SHA-256, fuentes, fechas y supuestos
  no son ornamento — son el producto.
- **El output debe tener nivel de auditoría financiera.**
  Si no podés firmarlo delante de un auditor, no lo publiques.

---

## REGLAS DE CONTENIDO

### Datos
- Solo mostrar datos reales con SHA-256 verificado.
- Nunca generar outputs con datos simulados, placeholders o valores
  inventados ("N/A", "—" es aceptable solo cuando el dato genuinamente
  no existe, no como fallback de un error).
- Métricas siempre con fuente y fecha visible.
- SHA-256 siempre visible y con al menos 16 caracteres del hash completo.

### Scores y estados
- **Nunca mostrar scores < 50 en outputs de comunicación externa.**
  (bricolage, certificados públicos, tarjetas)
- **Nunca mostrar estados de ALERTA en comunicación externa.**
  Las alertas son información interna de monitoreo, no de pitch.
- Excepción: en reportes certificados internos (faro_report_engine.py)
  los scores bajos y alertas son válidos — son para auditoría, no marketing.

### Zonas
- Solo incluir zonas con Sello Verde (SHA-256 verificado) en publicaciones.
- Ordenar zonas de mayor a menor score.
- Balcarce (70) · Amazonas (66) son las zonas ancla del pitch.
  Vaca Muerta y Rotterdam completan la cobertura global.

---

## REGLAS DE DISEÑO

### Paleta (obligatoria — no modificar sin aprobación)
```
Fondo principal  : #0a0a1a
Fondo secundario : #0d1020
Texto principal  : #f2ede4
Dorado (marca)   : #c9a84c
Dorado claro     : #e2c97e
Gris             : #888899
Verde (OK)       : #2d8c5e   → GREEN_L: #4ab87e
Rojo (alerta)    : #b03030   → RED_L:   #e05050
Azul (O&G)       : #2a6496   → BLUE_L:  #4a90c8
Ámbar (minería)  : #c07a30   → AMBER_L: #e09a50
```

### Tipografía
```
Títulos      : Cormorant Garamond (serif)
Texto general : Epilogue (sans)
Datos/hashes  : JetBrains Mono (monospace) — SIEMPRE para hashes y métricas
```

### Composición
- Sin títulos duplicados ni superpuestos en ningún panel.
- Sin zonas negras causadas por recortes incorrectos de PNG fuente.
- Sin pixelado ni artefactos de compresión en el output final.
- Sin texto cortado en los bordes del canvas.
- Cada panel de imagen debe mostrar la imagen completa, sin bordes negros
  en exceso (> 15% del área del panel).

### Resoluciones
- Bricolage LinkedIn: exactamente **1800 × 2400 px**
- Certificados: mínimo **1200 × 900 px** a 150 DPI
- Reportes: mínimo **2700 × 1950 px** a 150 DPI
- Dashboard: mínimo **3000 × 1650 px** a 150 DPI

### Sello VERIFIED
- Siempre visible, fondo verde `#2d8c5e` con opacidad 0.13, borde verde.
- Texto: "✓  VERIFIED  ·  SHA-256" en verde claro `#4ab87e`, bold.
- Si el sello no se puede verificar, NO mostrar el sello — mostrar
  "[ PENDING VERIFICATION ]" en gris. Nunca un sello falso.

---

## REGLAS DE CÓDIGO

### Antes de generar cualquier imagen
1. Verificar que todos los archivos fuente existen en disco.
2. Si falta un archivo, reportar el error con path completo y
   terminar sin generar output parcial.
3. Validar SHA-256 del PNG de fusión contra el archivo `.sha256`.
4. Si el hash no coincide, reportar y continuar solo si el modo
   es `internal` (reportes de auditoría). Para outputs externos: abortar.

### Post-generación
5. Calcular SHA-256 del output generado y guardarlo en `.sha256`.
6. Registrar la generación en `audit_log.jsonl`:
   - timestamp, tipo de output, área, score, estado, sha256, violaciones.

### Scores públicos
7. Si `score < 50`: no incluir el área en outputs de comunicación externa.
   Usar `mode='internal'` para reportes de auditoría.
8. Si `estado == 'ALERTA'`: ídem — solo en modo internal.

---

## CHECKLIST OBLIGATORIO

Antes de cada generación de output externo:

```
□ Archivos fuente existen en disco (PNG de fusión + .sha256)
□ SHA-256 verificado y coincide
□ Score >= 50 (o modo interno explícito)
□ Estado != 'ALERTA' (o modo interno explícito)
□ Sin títulos duplicados en paneles de imagen
□ Sin recortes incorrectos (PNG_Y_TOP calibrado)
□ Resolución correcta para el tipo de output
□ Sello VERIFIED visible y real
□ Generación registrada en audit_log.jsonl
□ Commit y push al terminar
```

---

## VIOLACIONES Y CONSECUENCIAS

| Violación | Consecuencia | Modo |
|-----------|-------------|------|
| Score < 50 en output externo | Área excluida automáticamente | Error bloqueante |
| ALERTA en output externo | Área excluida automáticamente | Error bloqueante |
| SHA-256 no coincide | Output abortado | Error bloqueante |
| Archivo fuente faltante | Output abortado | Error bloqueante |
| Título duplicado | Warning — revisar PNG_Y_TOP | Warning |
| Resolución incorrecta | Warning — verificar DPI y figsize | Warning |

---

## HISTORIAL DE CAMBIOS

| Versión | Fecha | Descripción |
|---------|-------|-------------|
| 1.0 | 2026-04-07 | Versión inicial — incorporada al pipeline |
