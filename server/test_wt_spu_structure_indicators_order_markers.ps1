# Markers: Structure tree lists assigned indicators; ↑↓ uses sort_order + narrow OPS.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$App = Get-Content -LiteralPath (Join-Path $Root "app.html") -Raw -Encoding UTF8

function Ok([bool]$Cond, [string]$Name) {
    if (-not $Cond) { throw "FAIL $Name" }
    Write-Host "OK $Name"
}

Ok ($App -match "function spuOrgTreeIndicatorsHtml") "tree indicators helper"
Ok ($App -match "function spuOrgMoveIndicatorSibling") "move indicator helper"
Ok ($App -match "spuOrgTreeIndicatorsHtml\(node\.org_node_id") "tree uses assigned indicators"
Ok ($App -match "spuOrgIndicatorsForNode\(nid\)") "move uses node list"
Ok ($App -match "wtPersistMgmtBranches\(\{ indicators: true \}") "narrow indicators OPS"
Ok ($App -match "indicatorIds:\s*\[a\.indicator_id,\s*b\.indicator_id\]") "persist two swapped ids"

$treeIdx = $App.IndexOf("function renderSpuOrgTreeHtml")
Ok ($treeIdx -ge 0) "renderSpuOrgTreeHtml present"
$treeSlice = $App.Substring($treeIdx, [Math]::Min(3500, $App.Length - $treeIdx))
Ok ($treeSlice -match "spuOrgTreeIndicatorsHtml") "tree render calls helper"
Ok ($treeSlice -notmatch "spuOrgRoutedValueHtml\(node") "tree no longer primary routes list"
Ok ($App -match "function spuOrgRoutedValueHtml") "routes helper kept"
Ok ($App -match "function spuOrgRoutesForNode") "routes for node kept"

$moveIdx = $App.IndexOf("function spuOrgMoveIndicatorSibling")
$moveSlice = $App.Substring($moveIdx, [Math]::Min(1800, $App.Length - $moveIdx))
Ok ($moveSlice -notmatch "input_order") "move does not touch input_order"
Ok ($moveSlice -match "a\.sort_order") "swaps sort_order"

$pfIdx = $App.IndexOf("function spuOrgIndicatorsForNode")
$pfSlice = $App.Substring($pfIdx, [Math]::Min(600, $App.Length - $pfIdx))
Ok ($pfSlice -match "sort_order") "PF list still sorts by sort_order"

Write-Host "OK test_wt_spu_structure_indicators_order_markers"
