import pandas as pd
import datetime
import os

def ejecutar_ciclo_automatico():
    print("\n🚀 [FARO ENGINE] MONITOR DE INTELIGENCIA SATELITAL")
    print("--------------------------------------------------")
    
    # 1. Definimos los lotes (Escalable a nivel GLOBAL)
    lotes = [
        {"lote": "Mogotes A1", "cultivo": "soja", "base_sar": -12.5},
        {"lote": "Balcarce N1", "cultivo": "maiz", "base_sar": -13.8},
        {"lote": "Loberia Sur", "cultivo": "trigo", "base_sar": -11.9}
    ]
    
    fecha_hoy = datetime.datetime.now().strftime("%d/%m/%Y")
    print(f"📅 Fecha de análisis: {fecha_hoy}")
    
    resultados = []
    for l in lotes:
        # Aquí el motor estima el rinde basado en la firma de radar 
        # Esto soluciona la falta de informes antiguos.
        print(f"🛰️ Procesando firma SAR para {l['lote']}...")
        
        # El rinde se calcula por la respuesta del radar (backscatter)
        rinde_estimado = round(abs(l["base_sar"]) * 0.6, 2)
        score = round(rinde_estimado / 10, 2)
        
        resultados.append({
            "lote": l["lote"],
            "cultivo": l["cultivo"],
            "rinde_tn_ha": rinde_estimado,
            "sar_backscatter_db": l["base_sar"],
            "score_faro": score
        })
    
    # 2. Actualizamos el JSON que lee la Web
    df = pd.DataFrame(resultados)
    df.to_json("data.json", orient="records")
    
    print("\n✅ CICLO COMPLETADO")
    print(df[["lote", "rinde_tn_ha", "score_faro"]])
    print("\n[INFO] data.json actualizado. Faro Protocol está listo.")

if __name__ == "__main__":
    ejecutar_ciclo_automatico()