# Markers stages A/B: RO delete-block diag + salary/vacation read without materialize.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function buildEmployeeVacationPlanDiagRows") "diag rows builder"
Ok ($app -match "function openEmployeeDeleteBlockDiag") "open delete-block diag"
Ok ($app -match "function runEmployeeDeleteBlockDiag") "run delete-block diag"
Ok ($app -match "loadPayloadForEmployeeDupCheck\(\)") "diag uses RO payload loader"
Ok ($app -match "employeeDeleteBlockDiagBtn") "admin diag button id"
Ok ($app -match "buildEmployeeVacationPlanDiagRows\(payload, eid, y\)") "diag filters by employee_id"
Ok ($app -match "force delete|force-delete|openEmployeeDeleteBlockDiag") "diag entry present"
Ok ($app -match "empDeleteBlockDiagId") "diag id input"
Ok ($app -notmatch "forceDeleteEmployee|forceRemoveEmployee") "no force-delete API"

# salaryRow: ensureSalary removed from view path
$salaryRowIdx = $app.IndexOf("function salaryRow(employee)")
Ok ($salaryRowIdx -ge 0) "salaryRow present"
$salaryRowSlice = $app.Substring($salaryRowIdx, [Math]::Min(500, $app.Length - $salaryRowIdx))
Ok ($salaryRowSlice -notmatch "ensureSalary\(") "salaryRow no ensureSalary"
Ok ($salaryRowSlice -match "calculateEmployee\(") "salaryRow still calculates"

Ok ($app -match "return vacationPlanStoredYearList\(y\)\.find") "findVacationPlanRec read-only list"
Ok ($app -match "function ensureVacationPlanRec\(") "ensureVacationPlanRec kept for writes"
Ok ($app -match "vacationPlanYearList\(y\)\.push\(rec\)") "write still creates via yearList"
Ok ($app -match "function applyPlannedVacationPayOnce\(") "once accrual kept"
Ok ($app -match "function peekSalaryMonth\(") "peekSalaryMonth kept"
Ok ($app -match "function setAdjustment\(") "edit salary path kept"
Ok ($app -match "ensureSalary\(employeeId\)") "ensureSalary kept for edits"

Write-Host "OK test_wt_salary_vacation_read_markers"
