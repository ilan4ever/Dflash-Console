"""Smoke-test every DFlash Console API endpoint against a running server.

Read-only by design: it only issues GETs (and a couple of harmless POSTs that
do not mutate engine state). State-changing endpoints (load/start/stop/unload,
download, import, create) are reported as "wired" from the OpenAPI schema and
are NOT executed here.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8900'
SID = 'gemma-31b-dflash'

# (method, path_template, params)
GET_TESTS = [
    ('GET', '/api/health', {}),
    ('GET', '/api/endpoints', {}),
    ('GET', '/api/config', {}),
    ('GET', '/api/installed', {}),
    ('GET', '/api/models', {}),
    ('GET', '/api/models?quick=1', {}),
    ('GET', '/api/servers', {}),
    ('GET', '/api/servers/profiles', {}),
    ('GET', '/api/runtimes', {}),
    ('GET', '/api/runtimes/manifests', {}),
    ('GET', '/api/runtimes/piper', {}),
    ('GET', '/api/runtimes/piper/voices', {}),
    ('GET', '/api/runtimes/piper/logs', {}),
    ('GET', '/api/runtimes/stt', {}),
    ('GET', '/api/runtimes/stt/logs', {}),
    ('GET', '/api/gpu-devices', {}),
    ('GET', '/api/gpu/contention', {}),
    ('GET', '/api/hardware', {}),
    ('GET', '/api/system-stats', {}),
    ('GET', '/api/runtime-recommendations', {}),
    ('GET', '/api/presets/export', {}),
    ('GET', '/api/docs/catalog', {}),
    ('GET', '/api/console/logs', {}),
    ('GET', '/api/console/logs?tail=20&errors_only=1', {}),
    ('GET', '/api/fs/browse', {'path': r'C:\dev\Dflash-Console'}),
    ('GET', '/api/models/vision/plan', {'path': r'C:\dev\Dflash-Console\models\google\gemma-4-12b-it-qat-q4_0-gguf\gemma-4-12b-it-qat-q4_0.gguf', 'server_id': SID}),
    ('GET', '/api/servers/{sid}/status', {}),
    ('GET', '/api/servers/{sid}/chat-ready', {}),
    ('GET', '/api/servers/{sid}/load-plan', {}),
    ('GET', '/api/servers/{sid}/inference-stats', {}),
    ('GET', '/api/logs/{sid}', {}),
    ('GET', '/api/logs/{sid}?tail=5', {}),
    ('GET', '/api/hf/search', {'q': 'gemma', 'limit': '2'}),
    ('GET', '/api/hf/local-match', {'repo_id': 'ggml-org/gemma-3-4b-it-GGUF', 'filename': 'gemma-3-4b-it-Q4_K_M.gguf'}),
    ('GET', '/api/hf/local-installs', {'repo_id': 'ggml-org/gemma-3-4b-it-GGUF'}),
    ('GET', '/api/hf/downloads', {}),
    ('GET', '/api/hf/download/nonexistent-job', {}),
    ('GET', '/api/stacks/capable-targets', {'model_path': r'C:\dev\Dflash-Console\models\google\gemma-4-12b-it-qat-q4_0-gguf\gemma-4-12b-it-qat-q4_0.gguf'}),
    ('GET', '/api/model-libraries/preview', {}),
    ('GET', '/api/model-libraries/import-plan', {'path': r'C:\dev\Dflash-Console\models\google\gemma-4-12b-it-qat-q4_0-gguf', 'preset': 'gguf'}),
    ('GET', '/api/model-libraries/scan', {'preset': 'dflash', 'path': r'C:\dev\Dflash-Console\models'}),
]

# Harmless POSTs that do not mutate engine state.
POST_TESTS = []

def call(method, path, params=None):
    url = BASE + path.format(sid=SID)
    if params:
        from urllib.parse import urlencode
        sep = '&' if '?' in url else '?'
        url += sep + urlencode(params)
    req = urllib.request.Request(url, method=method)
    if method == 'POST':
        req.add_header('Content-Type', 'application/json')
        req.data = json.dumps(params or {}).encode()
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, resp.getheader('Content-Type', '')
    except urllib.error.HTTPError as exc:
        return exc.code, ''
    except Exception as exc:  # noqa: BLE001
        return f'ERR {type(exc).__name__}: {exc}', ''

def main():
    results = []
    for method, path, params in GET_TESTS:
        status, ctype = call(method, path, params)
        tag = 'OK' if isinstance(status, int) and status < 300 else '??'
        results.append((tag, method, path, status))
    for method, path, params in POST_TESTS:
        status, ctype = call(method, path, params)
        tag = 'OK' if isinstance(status, int) and status < 300 else '??'
        results.append((tag, method, path, status))

    ok = sum(1 for r in results if r[0] == 'OK')
    print(f'{ok}/{len(results)} direct calls OK\n')
    for tag, method, path, status in results:
        print(f'[{tag}] {method:4s} {status} {path}')

    # Report which state-changing routes exist in OpenAPI (wired check only).
    try:
        schema = json.load(urllib.request.urlopen(BASE + '/openapi.json', timeout=20))
        paths = schema.get('paths', {})
        wired = sorted(p for p in paths if any(v in p for v in ('/load', '/stop', '/unload', '/start', '/listen', '/import', '/download', '/reload')))
        print(f'\nState-changing routes in OpenAPI ({len(wired)}):')
        for p in wired:
            print('  ', p)
    except Exception as exc:  # noqa: BLE001
        print('\nOpenAPI check failed:', exc)

if __name__ == '__main__':
    main()
