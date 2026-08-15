<?php
/**
 * Authenticated DFlash Console Windows update feed.
 *
 * Installed by the DFlash deployment script into the active OneVoice theme.
 */

if (!function_exists('dflash_console_update_root')) {
    function dflash_console_update_root(): string {
        $from_env = getenv('DFLASH_UPDATE_ROOT');
        if (is_string($from_env) && $from_env !== '') {
            return $from_env;
        }
        $local = __DIR__ . '/dflash-console-updates.local.php';
        if (is_readable($local)) {
            $value = include $local;
            if (is_string($value) && $value !== '') {
                return $value;
            }
        }
        return '';
    }
}

if (!function_exists('dflash_console_update_token')) {
    function dflash_console_update_token(): string {
        $path = dflash_console_update_root() . '/.token';
        return is_readable($path) ? trim((string) file_get_contents($path)) : '';
    }
}

if (!function_exists('dflash_console_update_tokens')) {
    function dflash_console_update_tokens(): array {
        $tokens = [];
        foreach ([
            dflash_console_update_root() . '/.token',
            dflash_console_update_root() . '/.token-legacy',
        ] as $path) {
            if (!is_readable($path)) {
                continue;
            }
            $contents = file_get_contents($path);
            if (!is_string($contents)) {
                continue;
            }
            foreach (preg_split('/\R+/', $contents) ?: [] as $token) {
                $token = trim((string) $token);
                if ($token !== '') {
                    $tokens[] = $token;
                }
            }
        }
        return array_values(array_unique($tokens));
    }
}

if (!function_exists('dflash_console_update_authorized')) {
    function dflash_console_update_authorized(): bool {
        $provided = isset($_GET['token']) ? (string) wp_unslash($_GET['token']) : '';
        if ($provided === '') {
            return false;
        }
        foreach (dflash_console_update_tokens() as $expected) {
            if (hash_equals($expected, $provided)) {
                return true;
            }
        }
        return false;
    }
}

if (!function_exists('dflash_console_update_json')) {
    function dflash_console_update_json(array $payload, int $status = 200): void {
        status_header($status);
        nocache_headers();
        header('Content-Type: application/json; charset=utf-8');
        echo wp_json_encode($payload);
        exit;
    }
}

if (!function_exists('dflash_console_serve_manifest')) {
    function dflash_console_serve_manifest(): void {
        if (!dflash_console_update_authorized()) {
            dflash_console_update_json(['error' => 'Unauthorized'], 403);
        }
        $path = dflash_console_update_root() . '/latest.json';
        if (!is_readable($path)) {
            dflash_console_update_json(['status' => 'not_available'], 404);
        }
        $manifest = json_decode((string) file_get_contents($path), true);
        if (!is_array($manifest)
            || ($manifest['appId'] ?? '') !== 'com.dflash.console'
            || !preg_match('/^DFlash-Console-Setup-\d+\.\d+\.\d+-x64\.exe$/i', (string) ($manifest['fileName'] ?? ''))
            || !is_file(dflash_console_update_root() . '/' . basename((string) ($manifest['fileName'] ?? '')))
        ) {
            dflash_console_update_json(['error' => 'Invalid release manifest'], 500);
        }
        dflash_console_update_json($manifest);
    }
}

if (!function_exists('dflash_console_serve_download')) {
    function dflash_console_serve_download(): void {
        if (!dflash_console_update_authorized()) {
            dflash_console_update_json(['error' => 'Unauthorized'], 403);
        }
        $manifest_path = dflash_console_update_root() . '/latest.json';
        $manifest = is_readable($manifest_path)
            ? json_decode((string) file_get_contents($manifest_path), true)
            : null;
        $file_name = is_array($manifest) ? basename((string) ($manifest['fileName'] ?? '')) : '';
        if (!preg_match('/^DFlash-Console-Setup-\d+\.\d+\.\d+-x64\.exe$/i', $file_name)) {
            dflash_console_update_json(['error' => 'Invalid release artifact'], 500);
        }
        $file_path = dflash_console_update_root() . '/' . $file_name;
        if (!is_file($file_path)) {
            dflash_console_update_json(['error' => 'Release artifact not found'], 404);
        }
        @set_time_limit(0);
        @ignore_user_abort(true);
        nocache_headers();
        header('Content-Description: File Transfer');
        header('Content-Type: application/vnd.microsoft.portable-executable');
        header('Content-Disposition: attachment; filename="' . $file_name . '"');
        header('Content-Length: ' . filesize($file_path));
        readfile($file_path);
        exit;
    }
}

add_action('init', function (): void {
    $uri = isset($_SERVER['REQUEST_URI']) ? strtok((string) $_SERVER['REQUEST_URI'], '?') : '';
    if ($uri === '/internal-app/dflash-console/latest.json') {
        dflash_console_serve_manifest();
    }
    if ($uri === '/internal-app/dflash-console/download') {
        dflash_console_serve_download();
    }
});
