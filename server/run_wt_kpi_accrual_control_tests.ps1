# Контрольные начисления KPI: WtKpiAccrualControlTests (синтетика, FakeConn, Node vm).
# Без Timeweb / рабочей БД. Формулы не меняются.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$pyDir = Join-Path $Root ".tools\python-embed"
$py = Join-Path $pyDir "python.exe"
$server = Join-Path $Root "server"
$helpersNode = Join-Path $env:LOCALAPPDATA "Programs\cursor\resources\app\resources\helpers\node.exe"
if (-not $env:WT_NODE -and (Test-Path $helpersNode)) {
    $env:WT_NODE = $helpersNode
}
if (-not (Test-Path $py)) {
    Write-Host "Portable Python not found at $py"
    exit 2
}
$link = "C:\wt_ca_server"
if (Test-Path $link) { cmd /c "rmdir `"$link`"" | Out-Null }
cmd /c "mklink /J `"$link`" `"$server`"" | Out-Null
@"
python312.zip
.
C:\wt_ca_server
"@ | Set-Content -Path (Join-Path $pyDir "python312._pth") -Encoding ASCII
try {
    & $py -m unittest test_wt_api.WtKpiAccrualControlTests -v
    $code = $LASTEXITCODE
} finally {
    if (Test-Path $link) { cmd /c "rmdir `"$link`"" | Out-Null }
    @"
python312.zip
.

# Uncomment to run site.main() automatically
#import site
"@ | Set-Content -Path (Join-Path $pyDir "python312._pth") -Encoding ASCII
}
exit $code
