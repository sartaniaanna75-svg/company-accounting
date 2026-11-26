# Markers: SPU stage-2 display format (display-only, operand unit, pf-in raw).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function mgmtRoundDisplay") "round display helper"
Ok ($app -match "function mgmtFormatDisplayNumber") "display number helper"
Ok ($app -match "function mgmtFormatMoneyDisplay") "money display helper"
Ok ($app -match "1e-10") "float-safe epsilon in round"

$fmtIdx = $app.IndexOf("function mgmtFormatValue(")
Ok ($fmtIdx -ge 0) "mgmtFormatValue present"
$fmtSlice = $app.Substring($fmtIdx, [Math]::Min(1200, $app.Length - $fmtIdx))
Ok ($fmtSlice -match 'unit === "percent"') "percent branch kept"
Ok ($fmtSlice -match "mgmtFormatDisplayNumber\(n, 2\)") "percent uses 2-digit display"
Ok ($fmtSlice -notmatch "Math\.round\(n \* 10\)") "percent no 1-digit Math.round*10"
Ok ($fmtSlice -match "mgmtFormatMoneyDisplay") "money uses money display"
Ok ($fmtSlice -notmatch "maximumFractionDigits:\s*0") "money not forced to 0 digits"

$pctIdx = $app.IndexOf("function mgmtFormatPct(")
$pctSlice = $app.Substring($pctIdx, [Math]::Min(400, $app.Length - $pctIdx))
Ok ($pctSlice -match "mgmtFormatDisplayNumber\(pct, 2\)") "mgmtFormatPct 2 digits"
Ok ($pctSlice -notmatch "Math\.round\(Number\(pct\) \* 10\)") "mgmtFormatPct no *10"

$chkIdx = $app.IndexOf("function spuIndFormulaCheckForPeriod(")
Ok ($chkIdx -ge 0) "formula check for period"
$chkSlice = $app.Substring($chkIdx, [Math]::Min(12000, $app.Length - $chkIdx))
Ok ($chkSlice -match "row\.unit") "formula check uses operand unit"
Ok ($chkSlice -notmatch 'mgmtFormatValue\(\{ unit: unit \}, row\.value') "no draft-unit for operands"

$explIdx = $app.IndexOf("function spuIndFormulaDraftExplainHtml(")
$explSlice = $app.Substring($explIdx, [Math]::Min(9000, $app.Length - $explIdx))
Ok ($explSlice -match "unit:\s*other\.unit") "explain rows carry operand unit"

$pfIdx = $app.IndexOf("function mgmtPfNumCell(")
$pfSlice = $app.Substring($pfIdx, [Math]::Min(2200, $app.Length - $pfIdx))
Ok ($pfSlice -match 'class="pf-view') "pf dual-mode view button"
Ok ($pfSlice -match "data-pf-raw") "edit raw stored in data-pf-raw"
Ok ($pfSlice -match "mgmtPfBeginEdit") "edit starts via beginEdit"
Ok ($pfSlice -match "mgmtFormatValue") "view state uses formatters"
Ok ($app -match "function mgmtParsePfCellInput") "PF parse helper present"
Ok ($app -match "function mgmtPfCellValuesEqual") "PF equal helper present"

Ok ($app -match "function setMgmtPlanValue") "plan save kept"
Ok ($app -match "function mgmtCompletionPct") "completion calc kept"

$nodeCandidates = @(
  (Get-Command node -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
  "$env:LOCALAPPDATA\Programs\cursor\resources\app\resources\helpers\node.exe",
  "$env:LOCALAPPDATA\Programs\Cursor\resources\app\resources\helpers\node.exe",
  "$env:ProgramFiles\nodejs\node.exe"
) | Where-Object { $_ -and (Test-Path $_) }
$node = if ($nodeCandidates) { $nodeCandidates[0] } else { $null }
Ok ($node -ne $null) "node available for harness"
$harness = Join-Path $PSScriptRoot "wt_spu_format_display_harness.js"
Ok (Test-Path $harness) "format harness file"
& $node $harness
if ($LASTEXITCODE -ne 0) { throw "FAIL: format display harness" }
Write-Host "OK harness wt_spu_format_display_harness"

Write-Host "OK test_wt_spu_format_display_markers"
