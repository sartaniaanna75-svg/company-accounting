# Маркеры узкого сохранения меню (navLayouts) — без live БД.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "app.html"))) { $root = $PSScriptRoot }
$app = Get-Content -Raw -Encoding UTF8 (Join-Path $root "app.html")
$api = Get-Content -Raw -Encoding UTF8 (Join-Path $root "server\wt_api.py")

function Ok([bool]$cond, [string]$msg) {
    if (-not $cond) { throw "FAIL: $msg" }
    Write-Host "OK: $msg"
}

Ok ($app -match "function wtSaveNavLayoutOp") "helper wtSaveNavLayoutOp"
Ok ($app -match "function saveNavLayoutEditor") "saveNavLayoutEditor present"
Ok ($app -match 'path = "navLayouts\." \+ key') "OPS path navLayouts.<code>"
Ok ($app -match "wtSaveNavLayoutOp\(key, nextLayout") "editor calls narrow saver"
Ok ($app -match "expected_v: typeof wtRecordVersion") "expected_v for conflict check"
Ok ($app -match "ok === true") "success only on confirmed ok"
Ok ($app -match "saveNavLayoutEditor\.busy") "busy guard"
Ok ($app -match "restoreIfStillOurs") "restore draft path on fail"
Ok ($app -match "forbidForcePut: true") "menu forbids broad PUT fallback"
Ok ($app -match "wtOpsUiEnabled\(true\)\) \{\s*\r?\n\s*if \(!options\.silentFail\)") "no-OPS refuses without saveApp"
Ok ($app -notmatch "function wtSaveNavLayoutOp\([\s\S]{0,900}?saveApp\(\)") "nav saver has no saveApp fallback"
Ok ($api -match '"navLayouts"') "server OPS allowlist has navLayouts"
Ok ($api -match 'if top == "navLayouts"') "server ACL for navLayouts"

Write-Host "All nav layout OPS markers OK."
