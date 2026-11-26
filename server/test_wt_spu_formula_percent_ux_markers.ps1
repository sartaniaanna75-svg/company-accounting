# Markers: percent formula UX hints (no blind *100 in format) ASCII.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function spuFormulaPercentUxHints") "percent ux hints helper"
Ok ($app -match "function spuFormulaIsOuterParenTimes100") "outer paren detector"
Ok ($app -match "function spuFormulaHasTrailingTimes100") "trailing times100 detector"
Ok ($app -match "spuFormulaPercentUxHints") "hints used"

$chkIdx = $app.IndexOf("function spuIndFormulaCheck(")
Ok ($chkIdx -ge 0) "formula check present"
$chkSlice = $app.Substring($chkIdx, [Math]::Min(9000, $app.Length - $chkIdx))
Ok ($chkSlice -match "spuFormulaPercentUxHints") "check shows percent hints"

Ok ($app -match "onSpuIndUnitChange") "unit change handler"
Ok ($app -match "spuIndUnitPercentNote") "percent unit note"

$fmtIdx = $app.IndexOf("function mgmtFormatValue(")
$fmtSlice = $app.Substring($fmtIdx, [Math]::Min(1200, $app.Length - $fmtIdx))
Ok ($fmtSlice -match 'unit === "percent"') "percent format kept"
Ok ($fmtSlice -match "mgmtFormatDisplayNumber\(n, 2\)") "percent display up to 2 digits"
Ok ($fmtSlice -notmatch "Math\.round\(n \* 10\)") "percent not forced to 1 digit"
Ok ($fmtSlice -notmatch "n \* 100") "percent branch has no n*100 scale"
Ok ($app -match "function mgmtRoundDisplay") "display round helper present"

Write-Host "OK test_wt_spu_formula_percent_ux_markers"
