# Run WtOpsFoundationTests with portable embeddable Python (no system install).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$pyDir = Join-Path $Root ".tools\python-embed"
$py = Join-Path $pyDir "python.exe"
$server = Join-Path $Root "server"
if (-not (Test-Path $py)) {
    Write-Host "Portable Python not found at $py"
    Write-Host "Place embeddable CPython under .tools/python-embed or install Python."
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
    & $py -m unittest `
        test_wt_api.WtOpsFoundationTests `
        test_wt_api.WtOpsIntegrationTests `
        test_wt_ops_pg `
        -v
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
