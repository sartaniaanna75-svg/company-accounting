# Маркеры минимального аутсорсинга в зарплате WorkTime (без live БД).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "app.html"))) { $root = $PSScriptRoot }
$app = Get-Content -Raw -Encoding UTF8 (Join-Path $root "app.html")
$api = Get-Content -Raw -Encoding UTF8 (Join-Path $root "server\wt_api.py")

function Ok([bool]$cond, [string]$msg) {
    if (-not $cond) { throw "FAIL: $msg" }
    Write-Host "OK: $msg"
}

Ok ($app -match "function isOutsourcingEmployee") "helper isOutsourcingEmployee"
Ok ($app -match "function outsourcingFixedAmount") "helper outsourcingFixedAmount"
Ok ($app -match "function outsourcingEmployees") "list outsourcingEmployees"
Ok ($app -match "setSalaryInner\('outsourcing'\)") "salary tab Аутсорсинг"
Ok ($app -match "empFormOutsourcing") "create form checkbox"
Ok ($app -match "empFormFixedPay") "create form fixed pay"
Ok ($app -match "function setEmployeeOutsourcingFlag") "edit flag handler"
Ok ($app -match "function wtSaveOutsourcingCardOps") "narrow OPS saver"
Ok ($app -match "function employeeBlockedFromEnablingOutsourcing") "staff protect helper"
Ok ($app -match "employeeBlockedFromEnablingOutsourcing\(employeeId\)") "block check before enable"
Ok ($app -match "employeeInAnyTimesheetRoster") "roster guard helper"
Ok ($app -match "employeeHasTimesheetMarks") "marks guard helper"
Ok ($app -match 'base \+ "\.outsourcing"') "OPS path outsourcing flag"
Ok ($app -match 'base \+ "\.outsourcingFixedPay"') "OPS path fixed pay"
Ok ($app -match "isOutsourcingEmployee\(employee\)") "calculateEmployee branch / filters"
Ok ($app -match "isOutsourcingEmployee\(getEmployee\(toId\)\)") "timesheet pick blocks outsourcing"
Ok ($app -match "isOutsourcingEmployee\(employee\)\) \{\s*\n\s*toast") "toggleAssign blocks outsourcing"
Ok ($app -match "includeInMonthPayroll") "payroll filter hook present"
Ok ($app -match "if \(isOutsourcingEmployee\(employee\)\) return false;") "exclude from regular payroll"
Ok ($app -match "mgmtEmployeeParticipatesInSpu") "SPU uses existing meeting flag"
Ok ($app -notmatch "isOutsourcingEmployee\([^\)]*\)[^\n]{0,80}participates_in_spu") "outsourcing does not gate SPU flag"
Ok ($api -match "outsourcingFixedPay") "server create field"
Ok ($api -match 'employee\.get\("outsourcing"\) is not True') "server skip employeeIds"

Write-Host "All outsourcing markers OK."
