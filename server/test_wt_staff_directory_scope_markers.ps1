# Markers: staff_directory scope company / territory / shop / single dept (ASCII).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function spuStaffDirectoryDeptCodes") "deptCodes helper"
Ok ($app -match "function spuStaffDirectoryScopeLabel") "scope label helper"
Ok ($app -match 'raw === "territory"') "territory sentinel"
Ok ($app -match "TERRITORY_DEPT_CODES") "reuses TERRITORY_DEPT_CODES"

$codesIdx = $app.IndexOf("function spuStaffDirectoryDeptCodes(")
Ok ($codesIdx -ge 0) "deptCodes present"
$codesSlice = $app.Substring($codesIdx, [Math]::Min(900, $app.Length - $codesIdx))
Ok ($codesSlice -match "TERRITORY_DEPT_CODES") "territory expands via TERRITORY_DEPT_CODES"
Ok ($codesSlice -match "DEPT_DEFAULTS") "empty expands via DEPT_DEFAULTS"

$factIdx = $app.IndexOf("function mgmtStaffDirectoryFact(")
$factSlice = $app.Substring($factIdx, [Math]::Min(900, $app.Length - $factIdx))
Ok ($factSlice -match "spuStaffDirectoryDeptCodes") "fact uses deptCodes"
Ok ($factSlice -match "seen\[e\.id\]") "unique employee_id kept"

$uiIdx = $app.IndexOf('else if (type === "staff_directory")')
Ok ($uiIdx -ge 0) "staff_directory UI branch"
$uiSlice = $app.Substring($uiIdx, [Math]::Min(1200, $app.Length - $uiIdx))
Ok ($uiSlice -match "value=`"territory`"") "UI territory option"
Ok ($uiSlice -match "spu-src-dept") "same select class kept"
Ok ($uiSlice -match "territory") "scope UI present"

Ok ($app -match "function spuResolveStaffDirectoryDeptCode") "resolve helper kept"
Ok ($app -match "sourceDeptId != null") "nullish All check kept"
Ok ($app -match "function mgmtReadStaffDirectoryValue") "read wrapper kept"
Ok ($app -match "function employeeCountsInStaffDirectoryOnDate") "asOf filter kept"
Ok ($app -match "function spuSafeDiv") "div-by-zero helper kept"
Ok ($app -match "function spuStaffDirectoryMirrorDeptId") "mirror dept helper"
Ok ($app -match "spuStaffDirectoryMirrorDeptId") "mirror used somewhere"

$saveIdx = $app.IndexOf("function saveSpuOrgIndicator(")
Ok ($saveIdx -ge 0) "saveSpuOrgIndicator present"
$saveNext = $app.IndexOf("`nfunction ", $saveIdx + 10)
$saveBody = $app.Substring($saveIdx, [Math]::Min(12000, $saveNext - $saveIdx))
Ok ($saveBody -match "spuStaffDirectoryMirrorDeptId") "save uses mirror for staff_directory"
Ok ($saveBody -match "staff_directory") "staff_directory branch in save"

$openIdx = $app.IndexOf("function openMgmtSource(")
$openSlice = $app.Substring($openIdx, [Math]::Min(900, $app.Length - $openIdx))
Ok ($openSlice -match 'source_type === "staff_directory"') "openMgmtSource skips workspace switch for staff_directory"

Write-Host "OK test_wt_staff_directory_scope_markers"
