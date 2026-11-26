# Markers: SPU stage 2a PF dual-mode edit + breakdown source unit.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function mgmtParsePfCellInput") "parse helper"
Ok ($app -match "function mgmtPfCellValuesEqual") "equal helper"
Ok ($app -match "function mgmtPfBeginEdit") "begin edit"
Ok ($app -match "function mgmtPfCommitEdit") "commit edit"
Ok ($app -match "function mgmtPfCancelEdit") "cancel edit"
Ok ($app -match "function mgmtPfUnitForSource") "source unit helper"
Ok ($app -match "mgmtPfBeginEdit\(this\)") "pf-view starts edit"
Ok ($app -match 'class="pf-view') "pf-view class"
Ok ($app -match "data-pf-raw") "raw from data attr"
Ok ($app -match "Escape") "Escape cancel"
Ok ($app -match "fromEnter") "Enter commit path"
Ok ($app -match "mgmtPfEditState") "edit state guard"

$pfIdx = $app.IndexOf("function mgmtPfNumCell(")
$pfSlice = $app.Substring($pfIdx, [Math]::Min(2200, $app.Length - $pfIdx))
Ok ($pfSlice -match "pf-view") "num cell uses pf-view"
Ok ($pfSlice -notmatch 'input class="pf-in" value="' ) "no permanent raw pf-in in num cell"

$brIdx = $app.IndexOf('class="pf-break-row"')
Ok ($brIdx -gt 0) "breakdown row"
$brSlice = $app.Substring($brIdx, [Math]::Min(800, $app.Length - $brIdx))
Ok ($brSlice -match "mgmtPfUnitForSource") "breakdown uses source unit"
Ok ($brSlice -notmatch "mgmtFormatValue\(it, fa") "breakdown not parent unit for fa"

Ok ($app -match "function setMgmtPfPlan") "setMgmtPfPlan kept"
Ok ($app -match "canPlan = canEdit && kind === `"plan`"") "canPlan unchanged"

# Plans tab still raw (not dual-mode)
$plansIdx = $app.IndexOf("function renderSpuPlansTabHtml(")
if ($plansIdx -lt 0) { $plansIdx = $app.IndexOf("renderSpuPlansTabHtml") }
Ok ($plansIdx -ge 0) "plans tab present"

$nodeCandidates = @(
  (Get-Command node -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
  "$env:LOCALAPPDATA\Programs\cursor\resources\app\resources\helpers\node.exe",
  "$env:LOCALAPPDATA\Programs\Cursor\resources\app\resources\helpers\node.exe",
  "$env:ProgramFiles\nodejs\node.exe"
) | Where-Object { $_ -and (Test-Path $_) }
$node = if ($nodeCandidates) { $nodeCandidates[0] } else { $null }
Ok ($node -ne $null) "node for harness"
$harness = Join-Path $PSScriptRoot "wt_spu_pf_edit_format_harness.js"
& $node $harness
if ($LASTEXITCODE -ne 0) { throw "FAIL harness" }
Write-Host "OK harness"

Write-Host "OK test_wt_spu_pf_edit_format_markers"
