# Markers: SPU stage 3 — independent plan formula for manual/one_c.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function spuIndShowsStandalonePlanFormulaUi") "standalone plan ui helper"
Ok ($app -match 'k === "manual" \|\| k === "one_c"') "manual/one_c in standalone helper" 2>$null
# More reliable:
Ok ($app -match 'return k === "worktime" \|\| k === "manual" \|\| k === "one_c"') "standalone kinds"

$kindIdx = $app.IndexOf("function onSpuIndKindChange(")
$kindSlice = $app.Substring($kindIdx, [Math]::Min(4500, $app.Length - $kindIdx))
Ok ($kindSlice -match "spuIndShowsStandalonePlanFormulaUi") "kind change uses standalone helper"

$boxIdx = $app.IndexOf("function renderSpuIndWorktimePlanFormulaBox(")
$boxSlice = $app.Substring($boxIdx, [Math]::Min(2500, $app.Length - $boxIdx))
Ok ($boxSlice -match "spuIndShowsStandalonePlanFormulaUi") "plan box gate uses helper"
Ok ($boxSlice -notmatch 'spuIndFormKind !== "worktime"') "plan box not worktime-only"

$saveIdx = $app.IndexOf("if (kind === `"manual`" || kind === `"one_c`")")
if ($saveIdx -lt 0) { $saveIdx = $app.IndexOf('if (kind === "manual" || kind === "one_c")') }
Ok ($saveIdx -gt 0) "manual/one_c save branch"
$saveSlice = $app.Substring($saveIdx, [Math]::Min(5500, $app.Length - $saveIdx))
Ok ($saveSlice -match "spuCommitSourceVersion") "manual save uses version commit"
Ok ($saveSlice -match "plan_calc_mode = spuIndFormPlanCalcOn") "manual save sets plan mode from checkbox"
Ok ($saveSlice -notmatch 'it\.plan_formula_expr = \[\];\s*\r?\n\s*it\.plan_calc_mode = "manual"') "manual save does not wipe plan_* pair"

$allowIdx = $app.IndexOf("const allowPlanFormula")
$allowSlice = $app.Substring($allowIdx, [Math]::Min(400, $app.Length - $allowIdx))
Ok ($allowSlice -match 'kind === "manual"') "allowPlanFormula includes manual"
Ok ($allowSlice -match 'kind === "one_c"') "allowPlanFormula includes one_c"

$nodeCandidates = @(
  (Get-Command node -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
  "$env:LOCALAPPDATA\Programs\cursor\resources\app\resources\helpers\node.exe",
  "$env:LOCALAPPDATA\Programs\Cursor\resources\app\resources\helpers\node.exe",
  "$env:ProgramFiles\nodejs\node.exe"
) | Where-Object { $_ -and (Test-Path $_) }
$node = if ($nodeCandidates) { $nodeCandidates[0] } else { $null }
Ok ($node -ne $null) "node"
& $node (Join-Path $PSScriptRoot "wt_spu_plan_fact_independent_harness.js")
if ($LASTEXITCODE -ne 0) { throw "FAIL harness" }
Write-Host "OK harness"

Write-Host "OK test_wt_spu_plan_fact_independent_markers"
