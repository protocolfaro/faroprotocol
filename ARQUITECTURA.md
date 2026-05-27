# FARO PROTOCOL — ARQUITECTURA BASE
**Versión 1.0 — 26/05/2026**

---

## PRINCIPIO RECTOR

Un violín. El mismo instrumento para todos los clientes.  
Agregar un cliente nuevo = copiar template + completar config JSON + prender módulos. Un día de trabajo.

---

## ESTRUCTURA DE REPOS

```
protocolfaro/
├── faroprotocol/          # Repo principal — backend + frontends de clientes
│   ├── railway-backend/   # Python — pipeline de datos + emails + API
│   ├── {cliente}/         # Un directorio por cliente
│   │   ├── index.html     # UN solo HTML por cliente (todos los paneles adentro)
│   │   ├── {cliente}_data.json     # Datos generados por el pipeline
│   │   ├── config_{cliente}.json  # Configuración del cliente
│   │   ├── heatmaps/      # PNGs satelitales generados
│   │   └── cronograma_semana.jpg  # Cronograma semanal (si aplica)
│   └── .github/workflows/ # GitHub Actions — emails semanales por cliente
│
└── faro-paneles/          # Repo público — paneles legacy (migrar a faroprotocol)
```

---

## REGLAS DE ARQUITECTURA

**REGLA 1 — UN HTML POR CLIENTE**  
Cada cliente tiene exactamente un archivo `index.html`.  
Todos los paneles de usuarios del cliente viven en ese archivo como secciones (`#roger`, `#juan`, etc).  
Nunca crear subpáginas separadas (`admin-x/`, `panel-y/`).

**REGLA 2 — ESPEJO DE ESTRUCTURA**  
Todos los clientes tienen exactamente la misma estructura de archivos.  
Lo que cambia es el contenido del JSON de config, no la estructura.

**REGLA 3 — MÓDULOS, NO CÓDIGO CUSTOM**  
Cada feature es un módulo que se prende o apaga por config.  
Si un cliente no tiene algo → `"modulo": false` → no aparece en el panel.  
Nunca escribir código específico para un cliente.

**REGLA 4 — DATOS VÍA JSON, NO HARDCODEADOS**  
Ningún dato de un cliente va hardcodeado en HTML o JS.  
Todo viene de `config_{cliente}.json` o `{cliente}_data.json`.

---

## CONFIG JSON POR CLIENTE

```json
{
  "cliente": "velez",
  "nombre": "Club Atlético Vélez Sarsfield",
  "sector": "futbol",
  "pipeline_dia": "lunes",
  "pipeline_hora": "07:00 ART",
  
  "modulos": {
    "heatmaps_ndvi": true,
    "heatmaps_sombra": true,
    "sistema_solar": true,
    "pileta": true,
    "polideportivo": true,
    "cronograma_canchas": true,
    "aspersores": true,
    "mediciones": true,
    "emails_semanales": true,
    "whatsapp_alertas": false
  },

  "usuarios": [
    {
      "id": "roger",
      "nombre": "Roger Matías Bernal",
      "rol": "canchero",
      "email": "correocancha@gmail.com",
      "adjuntos_email": ["canchero", "solar_v2"]
    },
    {
      "id": "juan",
      "nombre": "Juan González",
      "rol": "intendente",
      "email": "jgonzalez@velez.com.ar",
      "adjuntos_email": ["agro_FINAL", "solar_v2", "velez"]
    },
    {
      "id": "nelson",
      "nombre": "Nelson Pugliese",
      "rol": "comision",
      "email": "npugliese@velez.com.ar",
      "adjuntos_email": ["velez"]
    }
  ],

  "instalaciones": {
    "estadio": {
      "nombre": "Estadio José Amalfitani",
      "canchas": ["amalfitani", "1fp", "2fp"]
    },
    "villa_olimpica": {
      "nombre": "Villa Olímpica Raúl H. Gámez",
      "canchas": ["1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa"]
    },
    "polideportivo": {
      "nombre": "Polideportivo Feijóo",
      "sectores": ["basquet", "playon_norte"]
    },
    "pileta": {
      "nombre": "Complejo Acuático"
    }
  }
}
```

---

## MÓDULOS DISPONIBLES

| Módulo | Descripción | Sectores que lo usan |
|--------|-------------|----------------------|
| `heatmaps_ndvi` | Mapas de calor NDVI satelital por cancha | Fútbol, Agro |
| `heatmaps_sombra` | Mapas de sombra estructural + corrección manual | Fútbol |
| `sistema_solar` | Monitoreo de paneles solares | Fútbol, Energía |
| `pileta` | Calidad de agua y temperatura | Clubes con natación |
| `polideportivo` | Estado de canchas indoor | Clubes polideportivos |
| `cronograma_canchas` | Carga semanal de imagen del cronograma del club | Fútbol |
| `aspersores` | Control y registro de riego por cancha | Fútbol, Agro |
| `mediciones` | Registro manual de mediciones de campo | Fútbol, Agro |
| `emails_semanales` | Envío automático de reportes por rol | Todos |
| `whatsapp_alertas` | Alertas críticas por WhatsApp vía CallMeBot | Todos |
| `insar` | Deformación estructural satelital | Energía, Minería |
| `metano` | Detección de fugas TROPOMI | Energía, Upstream |
| `pasaporte_climatico` | Certificación climática de lotes | Agro, Seguros |

---

## PIPELINE DE DATOS

```
Cada lunes 07:00 ART (o día configurado)
        ↓
railway-backend/pipeline_{cliente}.py
        ↓
Fuentes: Open-Meteo + NASA FIRMS + Sentinel-2 + Copernicus
        ↓
Genera: {cliente}_data.json + heatmaps PNG
        ↓
Push a GitHub → GitHub Pages sirve los datos al panel
        ↓
GitHub Actions → emails con adjuntos por rol
```

---

## ONBOARDING DE CLIENTE NUEVO

1. Copiar `velez/` → `{cliente}/`
2. Crear `config_{cliente}.json` con los datos del cliente
3. Prender/apagar módulos según lo que tiene el cliente
4. Agregar coordenadas de instalaciones en el JSON
5. Copiar `railway-backend/pipeline_velez.py` → `pipeline_{cliente}.py`
6. Agregar variable de entorno en Railway: `{CLIENTE}_CONFIG`
7. Crear workflow en `.github/workflows/emails_{cliente}.yml`
8. Deploy — el panel está operativo

**Tiempo estimado: 1 día**

---

## CLIENTES ACTUALES

| Cliente | Estado | Módulos activos |
|---------|--------|-----------------|
| Vélez Sarsfield | ✅ Producción | heatmaps, solar, pileta, poli, cronograma, emails |
| Aldosivi | ⏳ Pendiente | heatmaps, emails |

---

## STACK TÉCNICO

- **Backend:** Python — FastAPI/Flask + APScheduler en Railway
- **Frontend:** HTML/CSS/JS vanilla — sin frameworks
- **Datos satelitales:** Sentinel-2 (Planetary Computer), NASA FIRMS, Copernicus
- **Emails:** GitHub Actions + Gmail SMTP
- **Hosting frontend:** GitHub Pages
- **Deploy backend:** Railway (auto-deploy desde GitHub)
- **Tipografía:** Cormorant Garamond (títulos) + Epilogue (cuerpo)
- **Color acento:** #c9a84c (dorado)
- **Fondo:** oscuro
