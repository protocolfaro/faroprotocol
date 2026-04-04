# Push a GitHub Pages — Instrucciones para Emilio

El repositorio ya tiene los commits listos. Solo falta autenticarte en GitHub y hacer el push.

---

## Estado actual

```
Rama    : master
Remote  : https://github.com/protocolfaro/faroprotocol.git
Commits pendientes de push:
  73105b6  feat: caso real Córdoba en web + 4 áreas globales preparadas
  b76359c  fix: bugs auditoria + limpieza archivos redundantes
```

---

## Paso 1 — Generá un Personal Access Token en GitHub

1. Abrí https://github.com/settings/tokens
2. Click en **"Generate new token (classic)"**
3. Nombre: `faroprotocol-push`
4. Expiration: 90 days (o la que prefieras)
5. Scope: tildar solo **`repo`** (primera opción)
6. Click en **"Generate token"**
7. Copiá el token — empieza con `ghp_...`
   ⚠️ Solo se muestra una vez. Guardalo en algún lado seguro.

---

## Paso 2 — Configurá la URL con el token

Abrí una terminal en la carpeta del proyecto y ejecutá esto
(reemplazá `TU_TOKEN` por el token que acabás de generar):

```bash
cd "C:\Users\Usuario\Desktop\Faro-index"
git remote set-url origin https://protocolfaro:TU_TOKEN@github.com/protocolfaro/faroprotocol.git
```

---

## Paso 3 — Push

```bash
git push origin master
```

Deberías ver algo como:
```
Enumerating objects: ...
Writing objects: 100% ...
To https://github.com/protocolfaro/faroprotocol.git
   643d6e1..73105b6  master -> master
```

---

## Paso 4 — Verificá en GitHub Pages

Abrí https://protocolfaro.github.io/faroprotocol y chequeá que:
- El hero chip muestre "Marcos Juárez · 61.0/100 · NDVI 0.44 · 2.69 t/ha"
- El track record muestre la tarjeta verde "✓ Validated / PILOT" con el SHA-256 real
- El ticker tenga las entradas de Córdoba piloto

GitHub Pages tarda 1-2 minutos en reflejar el cambio después del push.

---

## Si querés usar SSH en vez de token (opcional)

Si ya tenés una clave SSH configurada en GitHub:

```bash
git remote set-url origin git@github.com:protocolfaro/faroprotocol.git
git push origin master
```

---

## Si algo sale mal

Verificá que los commits están localmente:
```bash
git log --oneline -5
```

Verificá que el remote está bien configurado:
```bash
git remote -v
```

Cualquier duda: el estado del repo es limpio, no hay conflictos. Es solo autenticación.
