# Markers: per-indicator fact week start (Wed-Tue) without changing plans/global calendar (ASCII).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function mgmtSpuReportingWeekWithStartDay") "week with start day"
Ok ($app -match "function mgmtSpuWeekStartDayFor") "week start for indicator"
Ok ($app -match "function mgmtFactDaysForIndicator") "fact days helper"
Ok ($app -match "function mgmtIndicatorFactWindow") "fact window helper"
Ok ($app -match "function spuFormulaFactPeriodConflict") "formula period conflict"
Ok ($app -match "function spuCollectFormulaIndicatorIds") "nested formula walk"
Ok ($app -match "spuIndWeekStart") "UI week start select"
Ok ($app -match "reporting_week_start_day") "field persisted"
Ok ($app -match "spuFormulaFactPeriodConflict") "conflict referenced"

$saveIdx = $app.IndexOf("function saveSpuOrgIndicator(")
Ok ($saveIdx -ge 0) "save present"
$saveNext = $app.IndexOf("`nfunction ", $saveIdx + 10)
$saveBody = $app.Substring($saveIdx, [Math]::Min(14000, $saveNext - $saveIdx))
Ok ($saveBody -match "spuFormulaFactPeriodConflict") "save blocks period conflict"
Ok ($saveBody -match "reporting_week_start_day") "save writes week start"

$readIdx = $app.IndexOf("function mgmtReadIndicator(")
$readSlice = $app.Substring($readIdx, [Math]::Min(3500, $app.Length - $readIdx))
Ok ($readSlice -match "mgmtFactDaysForIndicator") "read uses fact days"

Ok ($app -match "factTip") "PF tip for wed fact"
Ok ($app -match "function setMgmtSpuWeekStartDay") "global setter kept"
Ok ($app -match "root\.reporting_week_start_day = 4") "global default thu kept"

Write-Host "OK test_wt_spu_indicator_week_start_markers"
