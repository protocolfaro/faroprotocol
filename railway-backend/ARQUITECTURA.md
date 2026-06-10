# Reglas Arquitectónicas — Faro Protocol Backend

## Regla 1: Assembler como única fuente de datos

**Ningún renderer, ningún `_body_*()`, ningún gen script importa `hermes`,
`velez_supabase`, `faro_ecostress` ni ninguna fuente de datos directamente.**

Todo dato pasa por `faro_assembler.assemble_report()`.  
Esta regla no tiene excepciones.

### Flujo de datos obligatorio

```
velez_data.json (estático)
velez_supabase (overlay)        →  faro_assembler.assemble_report()  →  VelezReport (dict)
hermes_consolidate (ET₀/humedad)                                           │
soil_metrics / vegetation_metrics (científico)                             ▼
                                                                   gen scripts (FARO_VD_PATH)
                                                                   _body_*() renderers
```

### Por qué existe esta regla

Antes del assembler cada módulo nuevo requería tocar los renderers.
`_body_roger()` creció a 274 líneas con imports inline de Hermes.
Los gen scripts originales fueron reemplazados por `render_reports.py` genérico.
Resultado: layouts rotos, datos pisados, reportes inconsistentes.

### Cómo agregar un módulo nuevo

1. Calcular y escribir el dato en su tabla Supabase (`soil_metrics`, `vegetation_metrics`, `climate_metrics`).
2. Agregar el campo como `Optional` en `faro_schema.py` (contrato canónico).
3. Leer el campo en `faro_assembler.py` dentro del paso correspondiente.
4. Los renderers y gen scripts lo leen desde el `VelezReport` ensamblado — sin tocar imports.

### Archivos de contrato

| Archivo | Rol |
|---|---|
| `sports/clients/velez/faro_schema.py` | TypedDict canónico — único lugar de definición de campos |
| `sports/clients/velez/faro_assembler.py` | Única función que lee fuentes de datos |
| `sports/clients/velez/velez_scheduler.py` | `_get_velez_data()` delega a `assemble_report()` |

## Regla 2: Un HTML por cliente, sin subpáginas

Cada cliente tiene exactamente un archivo HTML. No se crean subpáginas ni rutas adicionales.
`admin-roger` no existe — es un error arquitectónico.

## Regla 3: Gen scripts intocables

La estructura visual de los gen scripts (`gen_velez_*.py`) es fija.
Solo cambian datos vía `FARO_VD_PATH`. Nunca simplificar ni reemplazar con layouts genéricos.

## Regla 4: Sin constantes hardcodeadas de datos de campo

**Ningún gen script puede contener constantes hardcodeadas de datos de campo.**
Esto incluye temperaturas, índices espectrales, KPIs acuáticos, valores InSAR, conteos de paneles,
o cualquier número que represente estado físico del predio.

Todo dato viene del assembler (`assemble_report()` → `FARO_VD_PATH`).
Si no hay dato real → el campo es `None`/`null` → el renderer muestra `'SIN DATO'`.

Nunca un número inventado.
