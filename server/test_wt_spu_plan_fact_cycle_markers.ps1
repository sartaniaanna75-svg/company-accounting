# Markers: SPU stage 3a — plan/fact separated cycle graph (spuWouldCycle).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function spuDepNodeKey\(") "spuDepNodeKey"
Ok ($app -match "function spuDepParseNodeKey\(") "spuDepParseNodeKey"
Ok ($app -match "function spuCfgPlanFormulaActive\(") "spuCfgPlanFormulaActive"
Ok ($app -match "function spuDepEdgesFromNode\(") "spuDepEdgesFromNode"
Ok ($app -match "function spuWouldCycle\(") "spuWouldCycle"

$cycleIdx = $app.IndexOf("function spuWouldCycle(")
Ok ($cycleIdx -gt 0) "spuWouldCycle index"
$cycleSlice = $app.Substring($cycleIdx, [Math]::Min(5500, $app.Length - $cycleIdx))
Ok ($cycleSlice -match 'side === "plan" \|\| side === "fact" \|\| side === "both"') "side plan|fact|both"
Ok ($cycleSlice -match 'spuDepNodeKey\(indicatorId, "plan"') "start plan node"
Ok ($cycleSlice -match "spuVersionAt") "version-at week in wouldCycle"
Ok ($cycleSlice -match "color\[node\] === 1") "gray/black DFS"
Ok ($cycleSlice -match "isoAddDays") "week_offset via isoAddDays"

$edgesIdx = $app.IndexOf("function spuDepEdgesFromNode(")
$edgesSlice = $app.Substring($edgesIdx, [Math]::Min(2500, $app.Length - $edgesIdx))
Ok ($edgesSlice -match 'parsed\.kind === "plan"') "plan branch edges"
Ok ($edgesSlice -match "spuCfgPlanFormulaActive") "plan edges gated by plan_calc_mode"
Ok ($edgesSlice -match "resolveCfg\(parsed\.id, it, parsed\.woff\)") "resolveCfg gets week offset"

$depIdx = $app.IndexOf("function spuIndicatorDepIds(")
Ok ($depIdx -gt 0) "spuIndicatorDepIds present"
# Comment before function (look back) must keep deps for delete, not cycle-check.
$depHead = $app.Substring([Math]::Max(0, $depIdx - 220), 220)
Ok ($depHead -match "cycle-check") "depIds not for cycle"

# Call sites pass side
$saveIdx = $app.IndexOf("function saveSpuOrgIndicator(")
Ok ($saveIdx -gt 0) "saveSpuOrgIndicator"
$saveSlice = $app.Substring($saveIdx, [Math]::Min(22000, $app.Length - $saveIdx))
Ok (($saveSlice -split "spuWouldCycle\(").Count -ge 4) "three wouldCycle call sites in save"
Ok ($saveSlice -match 'spuWouldCycle\([^)]+"both"\)') "calculated/worktime side both"
Ok ($saveSlice -match 'spuIndFormPlanCalcOn \? "plan" : "both"') "manual side plan when plan formula on"

# Operand list does not filter by WouldCycle
$opIdx = $app.IndexOf("function spuFormulaOperandIndicators(")
$opSlice = $app.Substring($opIdx, [Math]::Min(1500, $app.Length - $opIdx))
Ok ($opSlice -notmatch "spuWouldCycle") "operand list not gated by WouldCycle"

$nodeCandidates = @(
  (Get-Command node -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
  "$env:LOCALAPPDATA\Programs\cursor\resources\app\resources\helpers\node.exe",
  "$env:LOCALAPPDATA\Programs\Cursor\resources\app\resources\helpers\node.exe",
  "$env:ProgramFiles\nodejs\node.exe"
) | Where-Object { $_ -and (Test-Path $_) }
$node = if ($nodeCandidates) { $nodeCandidates[0] } else { $null }
Ok ($node -ne $null) "node"
& $node (Join-Path $PSScriptRoot "wt_spu_plan_fact_cycle_harness.js")
if ($LASTEXITCODE -ne 0) { throw "FAIL harness" }
Write-Host "OK harness"

Write-Host "OK test_wt_spu_plan_fact_cycle_markers"
