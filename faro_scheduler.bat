@echo off
:: ============================================================
:: FARO PROTOCOL — Scheduler de precios semanal
:: Ejecutado automáticamente por Windows Task Scheduler
:: cada lunes a las 08:00 (hora de Argentina)
:: ============================================================

cd /d "C:\Users\Usuario\Desktop\Faro-index"

:: Timestamp de inicio
echo [%DATE% %TIME%] FARO scheduler iniciando... >> logs\scheduler.log

:: Correr actualización de precios
python faro_price_updater.py >> logs\scheduler.log 2>&1

:: Código de salida
if %ERRORLEVEL% EQU 0 (
    echo [%DATE% %TIME%] Completado OK >> logs\scheduler.log
) else (
    echo [%DATE% %TIME%] ERROR - codigo %ERRORLEVEL% >> logs\scheduler.log
)
