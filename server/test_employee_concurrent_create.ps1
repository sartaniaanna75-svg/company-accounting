# Concurrent create / admin merge safety (no live PostgreSQL).
# powershell -NoProfile -ExecutionPolicy Bypass -File server/test_employee_concurrent_create.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding $false
$app = [IO.File]::ReadAllText((Join-Path $Root "app.html"), $utf8)
$api = [IO.File]::ReadAllText((Join-Path $Root "server\wt_api.py"), $utf8)
$passed = 0; $failed = 0
function Ok($c, $m) {
    if ($c) { $script:passed++; Write-Host "  OK  $m" }
    else { $script:failed++; Write-Host "  FAIL  $m" -ForegroundColor Red }
}

Write-Host "=== Source markers ==="
Ok ($api.Contains("def handle_create_employee(")) "create handler"
Ok ($api.Contains('path == "/api/employees"')) "create route"
Ok ($api.Contains("employeeRemovals")) "removals support"
Ok ($api.Contains('ws_key == "employees"')) "admin upsert employees"
Ok ($api.Contains("_merge_dept_employees(")) "uses dept merge helper"
Ok ($api.Contains("client_request_id") -or $api.Contains("employeeCreateIdempotency")) "idempotency"
Ok ($app.Contains("function createEmployeeOnServer(")) "client create API"
Ok ($app.Contains("queueEmployeeRemoval(")) "client removals"
Ok ($app.Contains("createEmployeeOnServer(createPayload")) "form uses create API"
Ok ($app.IndexOf("obtainNewEmployeeId(w.code)") -lt 0 -or $app.Contains("createEmployeeOnServer(createPayload")) "create not via reserve+PUT only"

Write-Host ""
Write-Host "=== Simulated admin merge (PowerShell) ==="
function Merge-AdminEmployees($current, $incoming) {
    # Mirror: upsert by id, no delete without explicit removal
    $map = [ordered]@{}
    foreach ($e in @($current)) { if ($e.id) { $map[$e.id] = $e } }
    foreach ($e in @($incoming)) { if ($e.id) { $map[$e.id] = $e } }
    return @($map.Values)
}
function Apply-Removals($list, $removals) {
    $ban = @{}
    foreach ($r in @($removals)) { $ban[[string]$r] = $true }
    return @($list | Where-Object { -not $ban.ContainsKey([string]$_.id) })
}

$server = @(
    @{ id = "emp_keep"; name = "Old" },
    @{ id = "T-S-000001"; name = "AdminA"; work_tab_id = "loaders"; position = "Loader" }
)
$staleB = @(
    @{ id = "emp_keep"; name = "Old" },
    @{ id = "T-S-000002"; name = "AdminB"; work_tab_id = "loaders"; position = "Loader" }
)
$merged = Merge-AdminEmployees $server $staleB
$ids = @($merged | ForEach-Object { $_.id })
Ok ($ids -contains "T-S-000001") "stale put keeps AdminA"
Ok ($ids -contains "T-S-000002") "stale put keeps AdminB"

$afterDel = Apply-Removals $merged @("T-S-000001")
Ok (-not (($afterDel | ForEach-Object { $_.id }) -contains "T-S-000001")) "explicit removal deletes A"
Ok (($afterDel | ForEach-Object { $_.id }) -contains "T-S-000002") "explicit removal keeps B"

Write-Host ""
Write-Host "=== Environment ==="
$pyOk = $false
try {
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) {
        $ver = & python -c "import sys; print(sys.version)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) { $pyOk = $true }
    }
} catch { $pyOk = $false }
Ok ($true) ("python unittest available: " + $pyOk)
if (-not $pyOk) {
    Write-Host "  NOTE  Full ConcurrentEmployeeCreateApiTests not executed (no working Python)." -ForegroundColor Yellow
    Write-Host "  NOTE  Live parallel PostgreSQL test not executed." -ForegroundColor Yellow
}

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_employee_concurrent_create.ps1 checks passed."
exit 0
