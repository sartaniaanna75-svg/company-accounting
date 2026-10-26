# Интеграционные тесты ops на локальной PostgreSQL (worktime_test @ 127.0.0.1).
# Не подключается к Timeweb. Небезопасный URL — выход без SQL.
#
# Пример:
#   $env:WT_TEST_DATABASE_URL = "postgresql://worktime_test:SECRET@127.0.0.1:5432/worktime_test"
#   $env:WT_PIN_PEPPER = "unit-pepper"
#   powershell -NoProfile -ExecutionPolicy Bypass -File server/run_wt_ops_pg_tests.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$pyDir = Join-Path $Root ".tools\python-embed"
$py = Join-Path $pyDir "python.exe"
$server = Join-Path $Root "server"

if (-not (Test-Path $py)) {
    Write-Host "Нет portable Python: $py"
    exit 2
}

$url = ($env:WT_TEST_DATABASE_URL + "").Trim()
if (-not $url) {
    Write-Host "Задайте WT_TEST_DATABASE_URL, например:"
    Write-Host '  $env:WT_TEST_DATABASE_URL = "postgresql://worktime_test:ПАРОЛЬ@127.0.0.1:5432/worktime_test"'
    exit 2
}

# Предварительная проверка в PowerShell (дублирует Python-защиту).
$lower = $url.ToLowerInvariant()
foreach ($bad in @("timeweb", "twcstorage", "amazonaws", "supabase", "neon.tech")) {
    if ($lower.Contains($bad)) {
        Write-Host "ABORT: URL содержит запрещённый маркер '$bad'. SQL не выполняется."
        exit 3
    }
}
if ($lower -notmatch "://[^@]+@(127\.0\.0\.1|localhost)(:\d+)?/worktime_test(\?|$)") {
    Write-Host "ABORT: нужен хост 127.0.0.1|localhost и база worktime_test."
    Write-Host "Получено: $url"
    exit 3
}

if (-not $env:WT_PIN_PEPPER) { $env:WT_PIN_PEPPER = "unit-pepper" }
if (-not $env:WT_SCRYPT_N) { $env:WT_SCRYPT_N = "16" }
$env:WT_OPS_ENABLED = "1"
# Не используем DATABASE_URL из окружения IDE — только проверенный test URL.
$env:DATABASE_URL = $url

$link = "C:\wt_ca_server"
if (Test-Path $link) { cmd /c "rmdir `"$link`"" | Out-Null }
cmd /c "mklink /J `"$link`" `"$server`"" | Out-Null

# Embed: нужен site + psycopg. Включаем site в ._pth на время запуска.
$pthPath = Join-Path $pyDir "python312._pth"
$pthBackup = Get-Content $pthPath -Raw -ErrorAction SilentlyContinue
@"
python312.zip
.
C:\wt_ca_server
import site
"@ | Set-Content -Path $pthPath -Encoding ASCII

try {
    # Проверка psycopg
    & $py -c "import psycopg; print('psycopg', psycopg.__version__)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Пакет psycopg не найден в portable Python."
        Write-Host "Один раз установите (нужен интернет):"
        Write-Host "  1) Скачайте https://bootstrap.pypa.io/get-pip.py в .tools\get-pip.py"
        Write-Host "  2) .\.tools\python-embed\python.exe .\.tools\get-pip.py"
        Write-Host "  3) .\.tools\python-embed\python.exe -m pip install `"psycopg[binary]>=3.1,<4`""
        exit 4
    }

    Write-Host "=== Safety unit tests (без SQL к данным) ==="
    & $py -m unittest test_wt_ops_pg.WtOpsPgUrlSafetyTests -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host ""
    Write-Host "=== PostgreSQL integration (локальная worktime_test) ==="
    & $py -m unittest test_wt_ops_pg.WtOpsPostgresIntegrationTests -v
    $code = $LASTEXITCODE
} finally {
    if (Test-Path $link) { cmd /c "rmdir `"$link`"" | Out-Null }
    if ($null -ne $pthBackup) {
        Set-Content -Path $pthPath -Value $pthBackup -Encoding ASCII -NoNewline
    } else {
        @"
python312.zip
.

# Uncomment to run site.main() automatically
#import site
"@ | Set-Content -Path $pthPath -Encoding ASCII
    }
}

if ($code -eq 0) {
    Write-Host ""
    Write-Host "УСПЕХ: все PostgreSQL-тесты ops прошли."
} else {
    Write-Host ""
    Write-Host "ЕСТЬ ОШИБКИ: смотрите FAILED выше (exit=$code)."
}
exit $code
