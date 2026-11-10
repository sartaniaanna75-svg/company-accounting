# Markers: calculated + totals NeedsSetup uses formula check, not hard true (ASCII).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function spuIndicatorFormulaNeedsSetup") "formula needs-setup helper"
Ok ($app -match "function spuIndicatorNeedsSetup") "needs-setup present"

$idx = $app.IndexOf("function spuIndicatorNeedsSetup(")
Ok ($idx -ge 0) "needs-setup index"
$slice = $app.Substring($idx, [Math]::Min(2200, $app.Length - $idx))
Ok ($slice -match "spuIndicatorFormulaNeedsSetup") "totals/key use formula helper"
Ok ($slice -notmatch 'if \(kind === "calculated"\) return true') "hard calculated totals ban gone"
Ok ($slice -match 'layer === "totals"') "totals branch kept"
Ok ($slice -match 'layer === "key"') "key branch kept"

Ok ($app -match "function spuSettingsBucket") "bucket helper kept"

Write-Host "OK test_wt_spu_needs_setup_totals_calc_markers"
