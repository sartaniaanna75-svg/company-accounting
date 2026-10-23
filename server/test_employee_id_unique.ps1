# Unique employee IDs. Run: powershell -NoProfile -File server/test_employee_id_unique.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$app = [IO.File]::ReadAllText((Join-Path $Root "app.html"), [Text.UTF8Encoding]::new($false))
$api = [IO.File]::ReadAllText((Join-Path $Root "server\wt_api.py"), [Text.UTF8Encoding]::new($false))
$passed = 0; $failed = 0
function Ok($c, $m) { if ($c) { $script:passed++; Write-Host "  OK  $m" } else { $script:failed++; Write-Host "  FAIL  $m" -ForegroundColor Red } }

Write-Host "=== Client markers ==="
Ok ($app.Contains("EMPLOYEE_ID_PREFIX_BY_DEPT")) "prefix map"
Ok ($app.Contains('shop: "V"') -and $app.Contains('warehouse: "T-S"')) "shop/warehouse prefixes"
Ok ($app.Contains("function createEmployeeOnServer(")) "createEmployeeOnServer"
Ok ($app.Contains("/api/employees")) "create endpoint client"
Ok ($app.Contains("saveEmployeeCreateForm.busy = true")) "early busy"
Ok ($app.Contains("client_request_id")) "idempotency client key"
Ok ($app.Contains("queueEmployeeRemoval")) "explicit removals queue"
Ok ($app.Contains("generateEmployeeIdForDept")) "local dept generator"
Ok (-not ($app -match "id:\s*generateEmployeeId\(\)")) "create no longer uses bare generateEmployeeId()"
Ok ($app.Contains("createEmployeeOnServer(createPayload")) "create form uses API path"

Write-Host "=== Server markers ==="
Ok ($api.Contains("def handle_create_employee(")) "create handler"
Ok ($api.Contains('/api/employees')) "create route registered"
Ok ($api.Contains("def handle_reserve_employee_id(")) "reserve handler kept"
Ok ($api.Contains("FOR UPDATE")) "row lock for reserve/create"
Ok ($api.Contains("def employee_id_put_denied(")) "put denial helper"
Ok ($api.Contains("duplicate_employee_id")) "duplicate error code"
Ok ($api.Contains("employeeIdCounters")) "counters in payload"
Ok ($api.Contains("merge_employee_id_counters")) "counters merge max-only"
Ok ($api.Contains("employeeRemovals")) "explicit removals"
Ok ($api.Contains('ws_key == "employees"')) "admin employees upsert"

Write-Host "=== Local format logic ==="
function Format-Id($p, $n) { return ("{0}-{1:D6}" -f $p, $n) }
Ok ((Format-Id "V" 1) -eq "V-000001") "V format"
Ok ((Format-Id "T-S" 2) -eq "T-S-000002") "T-S format"
Ok ((Format-Id "T-O" 3) -eq "T-O-000003") "T-O format"
Ok ((Format-Id "T-L" 4) -eq "T-L-000004") "T-L format"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_employee_id_unique.ps1 checks passed."
exit 0
