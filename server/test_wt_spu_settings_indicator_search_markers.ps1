# Markers: SPU settings indicator search (UI filter only). ASCII-only checks.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "let mgmtSettingsIndicatorQ") "search state var"
Ok ($app -match "function mgmtSettingsIndicatorSearchQuery") "query helper"
Ok ($app -match "function mgmtSettingsIndicatorMatchesSearch") "match helper"
Ok ($app -match "function setMgmtSettingsIndicatorSearch") "set search"
Ok ($app -match "function clearMgmtSettingsIndicatorSearch") "clear search"
Ok ($app -match "function mgmtSettingsSearchBarHtml") "search bar html"
Ok ($app -match 'id="mgmtSettingsIndSearch"') "search input id"
Ok ($app -match "mgmtSettingsSearchBarHtml\(\)") "bar used in settings render"
Ok ($app -match "setMgmtSettingsIndicatorSearch\(this\.value\)") "live oninput"
Ok ($app -match "clearMgmtSettingsIndicatorSearch\(\)") "clear onclick"

$renderIdx = $app.IndexOf("function renderMgmtSettingsHtml")
Ok ($renderIdx -ge 0) "renderMgmtSettingsHtml present"
$renderSlice = $app.Substring($renderIdx, [Math]::Min(5500, $app.Length - $renderIdx))
Ok ($renderSlice -match "Tab badge counts use full bucket totals") "tab counts ignore search"
Ok ($renderSlice -match "mgmtSettingsIndicatorMatchesSearch") "list filtered by search"
Ok ($renderSlice -match "mgmtSettingsSearchBarHtml\(\)") "search bar in render"
Ok ($renderSlice -notmatch "saveApp\(\)") "settings render does not saveApp"

$setIdx = $app.IndexOf("function setMgmtSettingsIndicatorSearch")
$setSlice = $app.Substring($setIdx, [Math]::Min(500, $app.Length - $setIdx))
Ok ($setSlice -match "renderManagement\(\)") "search re-renders"
Ok ($setSlice -notmatch "saveApp\(\)") "search input does not saveApp"
Ok ($setSlice -match "mgmtSettingsIndSearch") "focus restore target"

$plansIdx = $app.IndexOf("function renderSpuPlansTabHtml")
$plansSlice = $app.Substring($plansIdx, [Math]::Min(6000, $app.Length - $plansIdx))
Ok ($plansSlice -match "mgmtSettingsIndicatorSearchQuery") "plans tab uses search"
Ok ($plansSlice -match "mgmtSettingsIndicatorMatchesSearch") "plans filters by name"

$layerIdx = $app.IndexOf("function setSpuIndicatorLayer")
$layerSlice = $app.Substring($layerIdx, [Math]::Min(350, $app.Length - $layerIdx))
Ok ($layerSlice -match "saveApp\(\)") "layer change still saves"

Write-Host "OK test_wt_spu_settings_indicator_search_markers"
