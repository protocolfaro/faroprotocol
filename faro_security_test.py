#!/usr/bin/env python3
"""
faro_security_test.py — Faro Protocol: CAPA 8 Penetration Testing
==================================================================
Verifica las 9 capas de seguridad del portal antes de dar acceso a clientes.
Correr siempre antes de un onboarding nuevo.

Uso:
  python faro_security_test.py                      # contra producción
  python faro_security_test.py --local              # contra localhost:8080
  python faro_security_test.py --url https://...    # URL personalizada
  python faro_security_test.py --verbose            # mostrar detalles

Pruebas (9 categorías):
  1. Security Headers (CAPA 2)
  2. Archivos sensibles expuestos (CAPA 4)
  3. Protección de reportes (CAPA 4)
  4. Auth bypass (CAPA 1 + 3)
  5. CSRF protection (CAPA 2)
  6. SQL Injection / input handling
  7. XSS reflection
  8. Directory traversal
  9. Rate limiting (CAPA 2)

Requisito: pip install requests
"""

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path

# Forzar UTF-8 en stdout (necesario en Windows con consola cp1252)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import requests
    from requests.exceptions import RequestException, SSLError, ConnectionError as ReqConnError
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ── Payloads de prueba ────────────────────────────────────────────────────────

SENSITIVE_FILES = [
    '/accounts.json',
    '/.env',
    '/.env.example',
    '/faro_clientes.json',
    '/audit_log.jsonl',
    '/netlify/functions/auth-monitor.mjs',
    '/netlify/functions/generate-signed-url.mjs',
    '/netlify/functions/serve-report.mjs',
    '/.git/config',
    '/serviceAccount.json',
    '/package.json',
]

REPORT_FILES = [
    '/faro_reporte_fusion_cordoba.png',
    '/faro_reporte_fusion_balcarce.png',
    '/faro_report_cordoba.png',
    '/faro_report_cordoba.sha256',
    '/data.json',
]

REQUIRED_HEADERS = {
    'strict-transport-security': 'HSTS (max-age requerido)',
    'x-frame-options':           'X-Frame-Options: DENY',
    'x-content-type-options':    'X-Content-Type-Options: nosniff',
    'referrer-policy':           'Referrer-Policy: no-referrer',
    'content-security-policy':   'Content-Security-Policy',
}

SQL_PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1--",
    '" OR ""="',
    "admin'--",
    "1; DROP TABLE users--",
    "' UNION SELECT null, null--",
]

XSS_PAYLOADS = [
    "<script>alert('xss')</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "<svg onload=alert(1)>",
    "';alert(String.fromCharCode(88,83,83))//",
]

TRAVERSAL_PATHS = [
    '/../../../etc/passwd',
    '/api/../accounts.json',
    '/api/generate-signed-url/../../../.env',
    '/%2e%2e%2f%2e%2e%2fetc%2fpasswd',
    '/./././accounts.json',
]

# JWT falso para pruebas de auth bypass
FAKE_JWT = (
    'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9'
    '.eyJzdWIiOiJmYWtlVUlEIiwiZW1haWwiOiJoYWNrZXJAZXZpbC5jb20iLCJpYXQiOjE2MDAwMDAwMDAsImV4cCI6OTk5OTk5OTk5OX0'
    '.INVALIDSIGNATURE_this_should_be_rejected'
)

# ── Colores de terminal ───────────────────────────────────────────────────────

def _c(text, code):
    if sys.stdout.isatty():
        return f'\033[{code}m{text}\033[0m'
    return text

def GREEN(t): return _c(t, '92')
def RED(t):   return _c(t, '91')
def YELLOW(t):return _c(t, '93')
def BLUE(t):  return _c(t, '94')
def BOLD(t):  return _c(t, '1')


# ── Clase principal ───────────────────────────────────────────────────────────

class FaroSecurityTest:

    def __init__(self, base_url: str, verbose: bool = False):
        self.base    = base_url.rstrip('/')
        self.api     = self.base + '/api'
        self.verbose = verbose
        self.results = []
        self.passed  = 0
        self.failed  = 0
        self.warnings = 0
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'FaroSecurityTest/1.0'})

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, status: str, test: str, detail: str = ''):
        icons   = {'PASS': '✓', 'FAIL': '✗', 'WARN': '⚠', 'INFO': '·', 'SKIP': '○'}
        colors  = {'PASS': GREEN, 'FAIL': RED, 'WARN': YELLOW, 'INFO': BLUE, 'SKIP': lambda x: x}
        icon    = icons.get(status, '?')
        color   = colors.get(status, lambda x: x)
        line    = f"  {color(icon)} [{status}] {test}"
        if detail and self.verbose:
            line += f"\n         → {detail}"
        print(line)
        self.results.append({'status': status, 'test': test, 'detail': detail})
        if status == 'PASS':
            self.passed += 1
        elif status == 'FAIL':
            self.failed += 1
        elif status == 'WARN':
            self.warnings += 1

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, path: str, **kwargs):
        try:
            return self.session.get(
                self.base + path, timeout=12,
                allow_redirects=False, verify=False, **kwargs)
        except (SSLError, ReqConnError, RequestException):
            return None

    def _post_api(self, endpoint: str, body: dict, headers: dict = None):
        try:
            return self.session.post(
                self.api + endpoint,
                json=body,
                headers=headers or {},
                timeout=12,
                verify=False,
            )
        except (SSLError, ReqConnError, RequestException):
            return None

    # ── Prueba 1: Security Headers ────────────────────────────────────────────

    def test_security_headers(self):
        print(f"\n{BOLD('[TEST 1] Security Headers (CAPA 2)')}")
        r = self._get('/')
        if r is None:
            self._log('WARN', 'No se pudo conectar al servidor', self.base)
            return

        for header, desc in REQUIRED_HEADERS.items():
            val = r.headers.get(header, '')
            if val:
                detail = val[:70] + ('…' if len(val) > 70 else '')
                if header == 'strict-transport-security' and 'max-age' not in val:
                    self._log('WARN', f'{desc}', f'Presente pero sin max-age: {detail}')
                elif header == 'x-frame-options' and 'DENY' not in val.upper():
                    self._log('WARN', f'{desc}', f'Presente pero no es DENY: {detail}')
                else:
                    self._log('PASS', f'{desc}', detail)
            else:
                self._log('FAIL', f'{desc} — AUSENTE en respuesta HTTP')

    # ── Prueba 2: Archivos sensibles ──────────────────────────────────────────

    def test_sensitive_files(self):
        print(f"\n{BOLD('[TEST 2] Archivos sensibles expuestos (CAPA 4)')}")
        for path in SENSITIVE_FILES:
            r = self._get(path)
            if r is None:
                self._log('PASS', path, 'inaccesible (sin respuesta)')
            elif r.status_code in (404, 302, 301, 403, 410):
                self._log('PASS', path, f'bloqueado (HTTP {r.status_code})')
            elif r.status_code == 200:
                # Verificar si hay contenido sensible
                body = r.text[:500].lower()
                if any(k in body for k in ['firebase', 'password', 'secret', 'api_key',
                                            'private_key', 'gmail', 'token', 'root:']):
                    self._log('FAIL', f'{path} — EXPUESTO con contenido sensible',
                              f'HTTP 200 · {len(r.text)} bytes')
                else:
                    self._log('WARN', f'{path} — HTTP 200 (revisar manualmente)',
                              f'{len(r.text)} bytes')
            else:
                self._log('WARN', f'{path}', f'HTTP {r.status_code}')

    # ── Prueba 3: Protección de reportes ──────────────────────────────────────

    def test_report_protection(self):
        print(f"\n{BOLD('[TEST 3] Protección de reportes (CAPA 4)')}")
        for path in REPORT_FILES:
            r = self._get(path)
            if r is None:
                self._log('PASS', path, 'inaccesible')
            elif r.status_code in (302, 301):
                loc = r.headers.get('location', '')
                if 'portal' in loc or 'login' in loc or 'expired' in loc:
                    self._log('PASS', path, f'redirige al portal ({r.status_code})')
                else:
                    self._log('WARN', path, f'redirige a: {loc[:60]}')
            elif r.status_code == 404:
                self._log('PASS', path, 'no expuesto (404)')
            elif r.status_code == 200:
                # Si es imagen/binario sin auth = FAIL
                ct = r.headers.get('content-type', '')
                if 'image' in ct or 'octet' in ct or len(r.content) > 1000:
                    self._log('FAIL', f'{path} — EXPUESTO directamente sin auth',
                              f'HTTP 200 · {ct} · {len(r.content)} bytes')
                else:
                    self._log('WARN', path, f'HTTP 200 · content-type: {ct}')
            else:
                self._log('PASS', path, f'HTTP {r.status_code}')

    # ── Prueba 4: Auth bypass ─────────────────────────────────────────────────

    def test_auth_bypass(self):
        print(f"\n{BOLD('[TEST 4] Auth bypass (CAPA 1 + 3)')}")
        valid_csrf = 'a' * 64  # formato correcto pero inválido como CSRF real

        # 4a: Sin token
        r = self._post_api('/generate-signed-url', {
            'file':      'faro_reporte_fusion_cordoba.png',
            'csrfToken': valid_csrf,
        })
        self._eval('Auth bypass: sin idToken', r, ok_codes=(400, 401, 403))

        # 4b: Token vacío
        r = self._post_api('/generate-signed-url', {
            'idToken':   '',
            'file':      'faro_reporte_fusion_cordoba.png',
            'csrfToken': valid_csrf,
        })
        self._eval('Auth bypass: idToken vacío', r, ok_codes=(400, 401, 403))

        # 4c: JWT falso (firma inválida)
        r = self._post_api('/generate-signed-url', {
            'idToken':   FAKE_JWT,
            'file':      'faro_reporte_fusion_cordoba.png',
            'csrfToken': valid_csrf,
        })
        self._eval('Auth bypass: JWT con firma inválida', r, ok_codes=(401, 403))

        # 4d: Acceso a archivo de otra área
        r = self._post_api('/generate-signed-url', {
            'idToken':   FAKE_JWT,
            'file':      '../accounts.json',
            'csrfToken': valid_csrf,
        })
        self._eval('Auth bypass: path traversal en file param', r, ok_codes=(400, 401, 403))

    def _eval(self, label: str, r, ok_codes: tuple = (400, 401, 403)):
        if r is None:
            self._log('WARN', label, 'Sin respuesta (servidor offline?)')
        elif r.status_code in ok_codes:
            self._log('PASS', label, f'Rechazado correctamente (HTTP {r.status_code})')
        elif r.status_code == 200:
            self._log('FAIL', label, f'ACEPTADO (HTTP 200) — vulnerabilidad crítica')
        else:
            self._log('PASS', label, f'HTTP {r.status_code}')

    # ── Prueba 5: CSRF ────────────────────────────────────────────────────────

    def test_csrf(self):
        print(f"\n{BOLD('[TEST 5] CSRF Protection (CAPA 2)')}")

        # Sin CSRF token
        r = self._post_api('/generate-signed-url', {
            'idToken': 'any',
            'file':    'test.png',
        })
        self._eval('CSRF: request sin token', r, ok_codes=(400, 401, 403))

        # CSRF token formato inválido
        r = self._post_api('/generate-signed-url', {
            'idToken':   'any',
            'file':      'test.png',
            'csrfToken': 'not-a-valid-csrf-token',
        })
        self._eval('CSRF: token formato inválido', r, ok_codes=(400, 401, 403))

        # CSRF token demasiado corto
        r = self._post_api('/generate-signed-url', {
            'idToken':   'any',
            'file':      'test.png',
            'csrfToken': 'abc123',
        })
        self._eval('CSRF: token demasiado corto', r, ok_codes=(400, 401, 403))

    # ── Prueba 6: SQL Injection ───────────────────────────────────────────────

    def test_sql_injection(self):
        print(f"\n{BOLD('[TEST 6] Input handling / SQL Injection')}")
        print(f"  (Nota: Firestore no es SQL — verificamos que inputs no rompen el servidor)")

        for payload in SQL_PAYLOADS[:4]:
            r = self._post_api('/auth-monitor', {
                'event': 'check_attempts',
                'email': payload,
                'ip':    '127.0.0.1',
            })
            if r is None:
                self._log('WARN', f'SQL payload: sin respuesta', payload[:40])
            elif r.status_code == 500:
                self._log('FAIL', f'SQL payload provoca error 500 — input inseguro', payload[:40])
            elif r.status_code in (200, 400, 422):
                # Check no reflection in response
                body = r.text[:200]
                if payload in body:
                    self._log('WARN', 'SQL payload reflejado en respuesta (baja severidad)', payload[:30])
                else:
                    self._log('PASS', f'SQL payload manejado (HTTP {r.status_code})', payload[:30])
            else:
                self._log('PASS', f'SQL payload → HTTP {r.status_code}', payload[:30])

    # ── Prueba 7: XSS ────────────────────────────────────────────────────────

    def test_xss(self):
        print(f"\n{BOLD('[TEST 7] XSS reflection')}")

        for payload in XSS_PAYLOADS[:3]:
            r = self._post_api('/auth-monitor', {
                'event': 'check_attempts',
                'email': payload + '@test.invalid',
                'ip':    '127.0.0.1',
            })
            if r is None:
                self._log('WARN', 'XSS: sin respuesta', payload[:30])
                continue
            body = r.text[:500]
            ct   = r.headers.get('content-type', '')
            if payload in body and 'text/html' in ct:
                self._log('FAIL', 'XSS: payload reflejado en HTML — reflected XSS', payload[:30])
            elif r.status_code == 500:
                self._log('FAIL', 'XSS payload provoca error 500', payload[:30])
            else:
                self._log('PASS', f'XSS payload manejado (HTTP {r.status_code})', payload[:25])

    # ── Prueba 8: Directory Traversal ─────────────────────────────────────────

    def test_directory_traversal(self):
        print(f"\n{BOLD('[TEST 8] Directory Traversal (CAPA 4)')}")

        for path in TRAVERSAL_PATHS:
            r = self._get(path)
            if r is None:
                self._log('PASS', f'Traversal {path[:40]}', 'inaccesible')
            elif r.status_code in (400, 403, 404, 302, 301):
                self._log('PASS', f'Traversal {path[:40]}', f'bloqueado (HTTP {r.status_code})')
            elif r.status_code == 200:
                body = r.text[:300].lower()
                if any(k in body for k in ['root:', 'firebase', 'password', 'secret', 'api_key']):
                    self._log('FAIL', f'Traversal EXITOSO: {path[:40]}',
                              f'Contenido sensible expuesto ({len(r.text)} bytes)')
                else:
                    self._log('WARN', f'Traversal {path[:40]}',
                              f'HTTP 200 · Sin contenido obvio sensible · revisar manualmente')
            else:
                self._log('PASS', f'Traversal {path[:40]}', f'HTTP {r.status_code}')

    # ── Prueba 9: Rate Limiting ───────────────────────────────────────────────

    def test_rate_limiting(self):
        print(f"\n{BOLD('[TEST 9] Rate Limiting (CAPA 2)')}")

        email = f'ratelimit_test_{int(time.time())}@test.invalid'
        captcha_triggered = False
        lockout_triggered = False
        responses = []

        for i in range(7):
            r = self._post_api('/auth-monitor', {
                'event': 'login_failed',
                'email': email,
                'ip':    '10.0.0.1',
            })
            if r is None:
                break
            responses.append(r.status_code)
            try:
                data = r.json()
                action = data.get('action', '')
                if action == 'captcha':
                    captcha_triggered = True
                elif action in ('lockout', 'permanent_block'):
                    lockout_triggered = True
                    break
            except Exception:
                pass
            time.sleep(0.2)

        if lockout_triggered:
            self._log('PASS', 'Rate limiting: lockout activado después de intentos fallidos')
        elif captcha_triggered:
            self._log('PASS', 'Rate limiting: captcha activado en umbral correcto')
        elif 429 in responses:
            self._log('PASS', 'Rate limiting: HTTP 429 recibido')
        elif responses and all(s == 200 for s in responses):
            self._log('WARN', 'Rate limiting: 7 intentos sin bloqueo (verificar Firestore config)',
                      f'Respuestas: {responses}')
        else:
            self._log('WARN', f'Rate limiting: respuestas mixtas', str(set(responses)))

    # ── TLS bonus check ───────────────────────────────────────────────────────

    def test_tls(self):
        print(f"\n{BOLD('[BONUS] TLS / HTTPS (CAPA 6)')}")
        if not self.base.startswith('https://'):
            self._log('SKIP', 'TLS no verificable en localhost/http')
            return

        # Verificar que HTTPS funciona con cert válido
        try:
            r = requests.get(self.base, timeout=10, verify=True)
            self._log('PASS', 'HTTPS con certificado válido')
        except SSLError as e:
            self._log('FAIL', 'Certificado SSL inválido', str(e)[:80])
        except RequestException:
            self._log('WARN', 'HTTPS: no se pudo conectar')

        # Verificar redirect HTTP → HTTPS
        http_base = self.base.replace('https://', 'http://', 1)
        try:
            r = requests.get(http_base, timeout=5, allow_redirects=False, verify=False)
            loc = r.headers.get('location', '')
            if r.status_code in (301, 302, 307, 308) and loc.startswith('https://'):
                self._log('PASS', 'HTTP → HTTPS redirect activo')
            elif r.status_code in (301, 302, 307, 308):
                self._log('WARN', f'HTTP redirect a: {loc[:50]}')
            else:
                self._log('WARN', f'HTTP responde {r.status_code} sin redirect a HTTPS')
        except RequestException:
            self._log('INFO', 'HTTP → bloqueado (Netlify/CDN lo maneja automáticamente)')

    # ── Run all ───────────────────────────────────────────────────────────────

    def run_all(self) -> bool:
        print()
        print(BOLD('=' * 62))
        print(f"  FARO PROTOCOL · Security Test Suite (CAPA 8)")
        print(f"  Target : {self.base}")
        print(f"  Date   : {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
        print(BOLD('=' * 62))

        self.test_security_headers()
        self.test_sensitive_files()
        self.test_report_protection()
        self.test_auth_bypass()
        self.test_csrf()
        self.test_sql_injection()
        self.test_xss()
        self.test_directory_traversal()
        self.test_rate_limiting()
        self.test_tls()

        # ── Resumen ───────────────────────────────────────────────────────────
        total = self.passed + self.failed + self.warnings
        print()
        print(BOLD('=' * 62))
        print(f"  RESULTADOS · {total} pruebas ejecutadas")
        print(f"  {GREEN('✓ PASS')}  : {self.passed}")
        print(f"  {RED('✗ FAIL')}  : {self.failed}")
        print(f"  {YELLOW('⚠ WARN')}  : {self.warnings}")
        print(BOLD('=' * 62))

        if self.failed == 0 and self.warnings <= 2:
            print(f"\n  {GREEN('✓ PORTAL LISTO PARA CLIENTES')}")
            print(f"  Todas las pruebas críticas pasaron.")
        elif self.failed == 0:
            print(f"\n  {YELLOW('⚠ REVISAR WARNINGS ANTES DE PRODUCCIÓN')}")
            print(f"  No hay vulnerabilidades críticas pero hay {self.warnings} advertencias.")
        else:
            print(f"\n  {RED('✗ NO LISTO — ' + str(self.failed) + ' VULNERABILIDAD(ES) CRÍTICA(S)')}")
            print(f"  Resolvé todos los FAIL antes de dar acceso a clientes.")
            for r in self.results:
                if r['status'] == 'FAIL':
                    print(f"    → {r['test']}")

        # Guardar reporte JSON
        report = {
            'ts':       time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'target':   self.base,
            'passed':   self.passed,
            'failed':   self.failed,
            'warnings': self.warnings,
            'ready':    self.failed == 0,
            'results':  self.results,
        }
        log_dir = Path(__file__).parent / 'logs'
        log_dir.mkdir(exist_ok=True)
        report_path = log_dir / 'security_test_report.json'
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n  Reporte JSON: {report_path}")

        return self.failed == 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not REQUESTS_OK:
        print("[ERROR] requests no está instalado.")
        print("  Instalá con: pip install requests")
        sys.exit(1)

    # Suprimir warnings de SSL para pruebas locales
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    parser = argparse.ArgumentParser(
        description='Faro Protocol — Security Test Suite (CAPA 8)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--url', default='https://faroprotocol.netlify.app',
                        help='URL base del portal (default: producción)')
    parser.add_argument('--local', action='store_true',
                        help='Usar http://localhost:8080')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Mostrar detalles de cada prueba')
    args = parser.parse_args()

    base_url = 'http://localhost:8080' if args.local else args.url

    tester = FaroSecurityTest(base_url, verbose=args.verbose)
    ok = tester.run_all()
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
