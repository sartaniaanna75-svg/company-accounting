# Markers: saveSpuOrgIndicator uses narrow OPS, not saveApp (ASCII-only).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

$fnIdx = $app.IndexOf("function saveSpuOrgIndicator(")
Ok ($fnIdx -ge 0) "saveSpuOrgIndicator present"
$nextIdx = $app.IndexOf("`nfunction ", $fnIdx + 10)
Ok ($nextIdx -gt $fnIdx) "function end found"
$body = $app.Substring($fnIdx, $nextIdx - $fnIdx)

Ok ($body -notmatch "saveApp\s*\(") "no saveApp in saveSpuOrgIndicator"
Ok ($body -match "wtPersistMgmtBranches") "uses wtPersistMgmtBranches"
Ok ($body -match "indicators:\s*true") "persists indicators"
Ok ($body -match "planIndicatorIds") "plans via planIndicatorIds when set"
Ok ($body -match "indicatorIds:\s*\[keepId\]") "narrow indicator id list"
Ok ($body -match 'okToast:\s*""') "suppress mid-chain okToast"
Ok ($body -match "spuOrgIndicatorPersistPaint") "paint helper after success"
Ok ($body -match "planIndicatorIds:\s*\[keepId\]") "plan stage present"
Ok ($body -match "if\s*\(\s*!okPlan\s*\)") "partial plan fail branch"
Ok ($body -match "okToast:\s*""""") "empty okToast on plan step"

Ok ($body -notmatch "management\.plans\.week:") "no week colon leaf path in saver"
Ok ($body -notmatch "wtDomainWriterSave") "no DomainWriter in saver"
Ok ($body -notmatch "wtCollectChangedLeaves") "no full collect in saver"

Ok ($body -match "kind === .manual.") "manual branch kept"
Ok ($body -match "onlyStaffDirectory") "staff_directory detection kept"
Ok ($body -match "spuStaffDirectoryMirrorDeptId") "staff_directory dept mirror on save"

$apiPath = Join-Path $PSScriptRoot "wt_api.py"
$api = Get-Content -Raw -Encoding UTF8 $apiPath
Ok ($api -match "WT_PATH_RE") "WT_PATH_RE present"
Ok ($api -match "len\(ops\) > 200") "ops batch limit 200 kept"

Write-Host "OK test_wt_spu_org_indicator_persist_markers"
