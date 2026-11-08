# Markers: staff_directory "All" dept resolve + frozen status through fromSources (ASCII).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function spuResolveStaffDirectoryDeptCode") "resolve helper"
Ok ($app -match "sourceDeptId != null") "nullish check for All"
Ok ($app -notmatch "src\.source_department_id \|\| \(parent && parent\.department_id\) \|\| `"`"") "old falsy || gone from read source"

$srcIdx = $app.IndexOf("function mgmtReadSourceFact(")
Ok ($srcIdx -ge 0) "mgmtReadSourceFact present"
$srcSlice = $app.Substring($srcIdx, [Math]::Min(1800, $app.Length - $srcIdx))
Ok ($srcSlice -match "spuResolveStaffDirectoryDeptCode") "read source uses resolve"
Ok ($srcSlice -match "source_type === `"timesheet`"") "timesheet branch unchanged name"

$fromIdx = $app.IndexOf("function mgmtReadFromSources(")
$fromSlice = $app.Substring($fromIdx, [Math]::Min(1600, $app.Length - $fromIdx))
Ok ($fromSlice -match "source_type === `"staff_directory`"") "fromSources staff_directory short-circuit"
Ok ($fromSlice -match "return mgmtReadSourceFact\(use\[0\]") "preserves full read status"

$deptIdx = $app.IndexOf("function spuStaffDirectoryDeptCode(")
$deptSlice = $app.Substring($deptIdx, [Math]::Min(700, $app.Length - $deptIdx))
Ok ($deptSlice -match "spuResolveStaffDirectoryDeptCode") "deptCode helper uses resolve"
Ok ($deptSlice -notmatch "source_department_id \|\| it\.department_id") "no falsy || in deptCode"

Ok ($app -match "function mgmtStaffDirectoryFact") "fact counter kept"
Ok ($app -match "function employeeCountsInStaffDirectoryOnDate") "asOf filter kept"
Ok ($app -match "function mgmtReadStaffDirectoryValue") "read wrapper kept"
Ok ($app -match 'status: "frozen"') "frozen status literal"
Ok ($app -match 'status: "unfrozen"') "unfrozen status literal"

Write-Host "OK test_wt_staff_directory_dept_all_markers"
