# Markers: metric catalog card exposes use_in_spu; picker empty states; no auto-unlink from card.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$App = Get-Content -LiteralPath (Join-Path $Root "app.html") -Raw -Encoding UTF8

function Ok([bool]$Cond, [string]$Name) {
    if (-not $Cond) { throw "FAIL $Name" }
    Write-Host "OK $Name"
}

Ok ($App -match "function metricCatalogSpuParticipationFieldsHtml") "card fields helper"
Ok ($App -match "function metricCatalogApplyUseInSpuFromForm") "apply from form helper"
Ok ($App -match "metricCatalogSpuParticipationFieldsHtml\(it\)") "form uses helper"
Ok ($App -match 'id="mcUseInSpu"') "checkbox id"
Ok ($App -match "mcUseInSpuBox") "spu box id"
Ok ($App -match "metricCatalogApplyUseInSpuFromForm\(item\)") "save applies flag"
Ok ($App -match "lockOff") "lock-off logic"

$applyIdx = $App.IndexOf("function metricCatalogApplyUseInSpuFromForm")
Ok ($applyIdx -ge 0) "apply helper index"
$applySlice = $App.Substring($applyIdx, [Math]::Min(1400, $App.Length - $applyIdx))
Ok ($applySlice -notmatch "setMetricUseInSpu") "card apply does not call setMetricUseInSpu"
Ok ($App -match "function setMetricUseInSpu") "sources setMetricUseInSpu kept"

$pickerIdx = $App.IndexOf("function spuIndSrcMetricPickerHtml")
Ok ($pickerIdx -ge 0) "picker function"
$pickerSlice = $App.Substring($pickerIdx, [Math]::Min(3200, $App.Length - $pickerIdx))
Ok ($pickerSlice -match "!\(allMetrics \|\| \[\]\)\.length") "empty participating branch"
Ok ($pickerSlice -match "filtered\.length") "filtered branch kept"

Ok ($App -match "function spuOrgTreeIndicatorsHtml") "structure tree helper intact"
Ok ($App -match "function spuOrgMoveIndicatorSibling") "structure move intact"

Write-Host "OK test_wt_metric_use_in_spu_card_markers"
