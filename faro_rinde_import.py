
import pandas as pd
import argparse

def run_demo():
    print("\n--- [FP] DEMO: IMPORTADOR DE RINDE ---")
    data = {
        "lote": ["Mogotes A1", "Balcarce N2", "Loberia Sur"],
        "rinde_kg_ha": [3500, 8200, 4100],
        "cultivo": ["soja", "maiz", "trigo"]
    }
    df = pd.DataFrame(data)
    print("\nDatos crudos detectados:")
    print(df)
    
    print("\nNormalizando: kg/ha -> tn/ha...")
    df["rinde_tn_ha"] = df["rinde_kg_ha"] / 1000
    
    print("\n[OK] Estadisticas generadas.")
    df.to_csv("rinde_modelo_ready.csv", index=False)
    print(">>> Archivo \"rinde_modelo_ready.csv\" creado en faro-index.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.demo: run_demo()

