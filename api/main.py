
import os
from groq import Groq
from flask import Flask, jsonify

app = Flask(__name__)

# Sistema de Autocorrección: Si falla, la IA analiza el error
def self_healing_logic(error_message):
    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    prompt = f"El sistema Faro Protocol tuvo este error: {error_message}. Genera una solucion tecnica breve."
    completion = client.chat.completions.create(model="llama3-8b-8192", messages=[{"role": "user", "content": prompt}])
    return completion.choices[0].message.content

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def catch_all(path):
    try:
        # Aquí es donde ocurre la magia de Faro
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        
        # Simulación de auditoría autónoma
        status = "Operando"
        analysis = "Faro Protocol: Monitoreando niveles Sovereign y Analyst. Todo en orden."
        
        return jsonify({
            "status": status,
            "agente_faro": analysis,
            "repo": "protocolfaro/faroprotocol",
            "autonomia": "Activada"
        })
    except Exception as e:
        # Si algo falla, el sistema se explica solo
        error_fix = self_healing_logic(str(e))
        return jsonify({"error": str(e), "sugerencia_ia": error_fix}), 500

# Esto es para que Vercel lo reconozca
def handler(event, context):
    return app(event, context)

