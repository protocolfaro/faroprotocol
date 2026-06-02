# FARO PROTOCOL — ARQUITECTURA DEL SISTEMA

## Verticales
- Faro Sports: clubes, predios deportivos, césped. Cliente base: Vélez Sarsfield.
- Faro Events: productoras, shows, certificación. Cliente base: Dale Play.
- Faro Protocol: Agro, Energía, Minería, Infraestructura crítica.

## Regla simple
- ¿Tiene césped? → Faro Sports
- ¿Tiene escenario? → Faro Events
- ¿Todo lo demás? → Faro Protocol

## Estructura de carpetas objetivo
core/ → satellite, insar, soil, weather, report, storage, utils
sports/ → modules + clients/velez/
events/ → modules + clients/dale-play/
protocol/ → agro, energy, mining (futuro)
api/ → Flask unificado

## Reglas que nunca se rompen
1. Core no conoce a Sports ni Events
2. Sports y Events importan del Core — nunca entre sí
3. Mejora en Core → disponible para todos automáticamente
4. Mejora específica de cliente → se queda en clients/
5. Nunca hardcodear datos — todo viene de API o JSON generado por pipeline
6. Si un dato no está disponible → N/D explícito, nunca valor falso
7. Todo módulo nuevo hereda de DalePlayModule o su equivalente en Sports
8. MANUAL.md se actualiza con append_to_manual() — nunca sobreescribir

## client_context
El pipeline recibe un CLIENT_ID y busca automáticamente en clients/{CLIENT_ID}/config.json.
Nunca hardcodear el nombre del cliente en el código.

## Certifier
La certificación SHA-256 vive en core/report/certification.py como clase Certifier.
Llamada: Core.Certifier.generate(data) desde cualquier vertical.

## cache/
Todo resultado de API externa se cachea en storage/cache/ antes de intentar la fuente real.
Nunca llamar dos veces a la misma API en el mismo pipeline run.

## utils/
Funciones transversales en core/utils/: formateo de fechas, manejo de coordenadas, logs de errores.
Nunca duplicar estas funciones en Sports o Events.
