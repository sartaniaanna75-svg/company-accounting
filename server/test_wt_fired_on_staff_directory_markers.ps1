# Маркеры: дата увольнения + источник численности СПУ (без live DB).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $PSScriptRoot "app.html"))) {
  # server/ рядом с корнем репо
  $root = Split-Path -Parent $PSScriptRoot
}
$app = Get-Content -Raw -Encoding UTF8 (Join-Path $root "app.html")
if (-not $app) { $app = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot "..\app.html") }

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function normalizeFiredOnIso") "normalizeFiredOnIso"
Ok ($app -match "function employeeVisibleInTimesheetMonth") "timesheet month filter"
Ok ($app -match "function wtSaveJobStatusFiredOnOps") "atomic jobStatus+fired_on OPS"
Ok ($app -match "function onEmployeeJobStatusChange") "jobStatus change asks date"
Ok ($app -match "staff_directory") "staff_directory source"
Ok ($app -match '\[\"staff_directory\"') "staff_directory UI option"
Ok ($app -match "function mgmtStaffDirectoryFact") "staff directory fact"
Ok ($app -match "function openStaffDirectoryFactModal") "manual freeze modal"
Ok ($app -match "function confirmStaffDirectoryFactModal") "confirm freeze only"
Ok ($app -notmatch "mgmtFreezePreviousStaffDirectoryWeek") "no auto freeze on read"
Ok ($app -match "requireStaffFactPerm") "fact save checks perm flag"
Ok ($app -notmatch "hired_on") "no hired_on field"
Ok ($app -match "includeInMonthPayroll") "payroll hook intact"

Write-Host "OK test_wt_fired_on_staff_directory_markers"
