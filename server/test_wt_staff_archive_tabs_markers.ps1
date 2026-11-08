# Markers: staff archive tabs (UI filter only).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "summaryStaffListMode") "list mode state"
Ok ($app -match "function setSummaryStaffListMode") "set mode"
Ok ($app -match "function employeeMatchesSummaryStaffListMode") "match helper"
Ok ($app -match "function syncSummaryStaffArchiveTabs") "sync tabs"
Ok ($app -match "summaryStaffArchiveTabs") "tabs host id"
Ok ($app -match "data-staff-list-mode=`"active`"") "active tab"
Ok ($app -match "data-staff-list-mode=`"archive`"") "archive tab"
Ok ($app -match "data-staff-list-mode=`"all`"") "all tab"
Ok ($app -match "employeeMatchesSummaryStaffListMode\(employee, listMode\)") "directory filter uses mode"
Ok ($app -match "const wasFired = !!\(emp && emp\.jobStatus === `"fired`"\)") "restore confirm gate"
Ok ($app -match "applyEmployeeJobStatusAndFiredOn\(employeeId, deptCode \|\| `"`", `"working`", `"`"\)") "restore uses working status"
Ok ($app -match "function wtSaveJobStatusFiredOnOps") "atomic status OPS kept"
Ok ($app -match "function employeeVisibleInTimesheetMonth") "timesheet filter untouched name"
Ok ($app -match "function includeInMonthPayroll") "payroll hook untouched name"
Ok ($app -match "function includeOutsourcingInMonth") "outsourcing month hook kept"

# Ensure historical helpers still have original bodies (not replaced by staff list mode)
$visIdx = $app.IndexOf("function employeeVisibleInTimesheetMonth")
Ok ($visIdx -ge 0) "visibleInTimesheet present"
$visSlice = $app.Substring($visIdx, [Math]::Min(400, $app.Length - $visIdx))
Ok ($visSlice -match "employeeFiredOnIso") "timesheet still uses fired_on"
Ok ($visSlice -notmatch "summaryStaffListMode") "timesheet not tied to archive tabs"

$payIdx = $app.IndexOf("function includeInMonthPayroll")
$paySlice = $app.Substring($payIdx, [Math]::Min(350, $app.Length - $payIdx))
Ok ($paySlice -match "employeeHasMonthActivity") "payroll still activity-based"
Ok ($paySlice -notmatch "summaryStaffListMode") "payroll not tied to archive tabs"

Write-Host "OK test_wt_staff_archive_tabs_markers"
