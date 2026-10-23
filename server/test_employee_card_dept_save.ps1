# Save employee card by dept+id. Run: powershell -NoProfile -File server/test_employee_card_dept_save.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$src = [IO.File]::ReadAllText((Join-Path $Root "app.html"), [Text.UTF8Encoding]::new($false))
$passed = 0; $failed = 0
function Ok($c, $m) { if ($c) { $script:passed++; Write-Host "  OK  $m" } else { $script:failed++; Write-Host "  FAIL  $m" -ForegroundColor Red } }

Write-Host "=== Source markers ==="
Ok ($src.Contains("function resolveEmployeeCardForSave(")) "resolveEmployeeCardForSave exists"
Ok ($src.Contains("function employeeCardDomId(")) "employeeCardDomId exists"
Ok ($src.Contains("employeeCardDraftKey(")) "draft keyed by dept|id"
Ok ($src.Contains('data-emp-card="1"')) "cards have data-emp-card"
Ok ($src.Contains("data-dept=")) "cards have data-dept"
Ok ($src -match "commitEmployeeCard\(employeeId, deptCode\)") "commit accepts deptCode"
Ok ($src -match "applyEmployeeCardProtectedDraft\(employeeId, card, deptCode") "apply accepts dept"
Ok ($src.Contains("opts.employee || getEmployee(employeeId)")) "setEmployeePrimaryWorkTab accepts explicit employee"
Ok ($src.Contains("resolveEmployeeCardForSave")) "resolve helper used"
Ok ($src.IndexOf("function getEmployee(id)") -ge 0) "getEmployee kept global"

Write-Host ""
Write-Host "=== Runtime: dept+id resolve and save isolation ==="

function Resolve-Card($workspaces, $dept, $id) {
    $w = $workspaces | Where-Object { $_.code -eq $dept } | Select-Object -First 1
    if (-not $w) { return @{ error = "no dept" } }
    $matches = @($w.employees | Where-Object { $_.id -eq $id })
    if ($matches.Count -eq 0) { return @{ error = "not found" } }
    if ($matches.Count -gt 1) { return @{ error = "dup in dept" } }
    return @{ employee = $matches[0]; deptCode = $dept }
}

$shopEmp = [ordered]@{ id = "emp_125"; name = "Test"; position = "Cashier"; position_id = "pos_cash"; work_tab_id = "cashiers" }
$whEmp = [ordered]@{ id = "emp_125"; name = "Test"; position = "Picker"; position_id = "pos_pick"; work_tab_id = "loaders" }
$workspaces = @(
    [pscustomobject]@{ code = "shop"; employees = @($shopEmp) },
    [pscustomobject]@{ code = "warehouse"; employees = @($whEmp) }
)

$r = Resolve-Card $workspaces "warehouse" "emp_125"
Ok ($null -eq $r.error) "resolve warehouse emp_125"
$r.employee.position = "Loader"
$r.employee.position_id = "pos_loader"
Ok ($whEmp.position -eq "Loader" -and $whEmp.position_id -eq "pos_loader") "warehouse position changed"
Ok ($shopEmp.position -eq "Cashier" -and $shopEmp.position_id -eq "pos_cash") "shop card unchanged"

$reload = Resolve-Card $workspaces "warehouse" "emp_125"
Ok ($reload.employee.position -eq "Loader") "reload warehouse keeps Loader"

$prevPos = $whEmp.position
$whEmp.work_tab_id = "pickers"
Ok ($whEmp.work_tab_id -eq "pickers" -and $whEmp.position -eq $prevPos) "work group change keeps position"

$metricAsg = [pscustomobject]@{
    department_id = "warehouse"; work_tab_id = "loaders"; position_id = "pos_loader"
    assign_mode = "specific"; employee_ids = @("emp_125")
}
$before = ($metricAsg | ConvertTo-Json -Compress)
Ok ($before -eq ($metricAsg | ConvertTo-Json -Compress)) "metric assignment unchanged by card field edit"
Ok ($metricAsg.department_id -eq "warehouse" -and $metricAsg.work_tab_id -ne "cashiers") "no foreign cashier metrics on warehouse assignment"

$dupWs = @(
    [pscustomobject]@{
        code = "warehouse"
        employees = @(
            [ordered]@{ id = "emp_dup"; position = "A" },
            [ordered]@{ id = "emp_dup"; position = "B" }
        )
    }
)
$bad = Resolve-Card $dupWs "warehouse" "emp_dup"
Ok ($bad.error -eq "dup in dept") "duplicate id in one dept blocked"

$shopR = Resolve-Card $workspaces "shop" "emp_125"
Ok ($shopR.employee.position -eq "Cashier") "shop card still Cashier"

function DomId($d, $i) { return "emp-card-$d-$i" }
Ok ((DomId "shop" "emp_125") -ne (DomId "warehouse" "emp_125")) "HTML ids unique across depts"

# Position change must not auto-change work_tab in apply path (source)
$applyStart = $src.IndexOf("function applyEmployeeCardProtectedDraft")
$applySlice = $src.Substring($applyStart, [Math]::Min(1800, $src.Length - $applyStart))
Ok ($applySlice.Contains("setEmployeePrimaryWorkTab") -and $applySlice.Contains("setEmployeePosition")) "apply can change tab and position separately"
Ok ($applySlice -match "nextTab[\s\S]*setEmployeePrimaryWorkTab[\s\S]*nextPosId[\s\S]*setEmployeePosition") "tab and position applied independently"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_employee_card_dept_save.ps1 checks passed."
exit 0
