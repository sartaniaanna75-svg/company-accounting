# Metrics employee name wrap (visual only). Run:
# powershell -NoProfile -ExecutionPolicy Bypass -File server/test_metrics_emp_name_wrap.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding $false
$src = [IO.File]::ReadAllText((Join-Path $Root "app.html"), $utf8)
$passed = 0; $failed = 0
function Ok($c, $m) {
    if ($c) { $script:passed++; Write-Host "  OK  $m" }
    else { $script:failed++; Write-Host "  FAIL  $m" -ForegroundColor Red }
}

Write-Host "=== Display helpers ==="
Ok ($src.Contains("function metricsEmployeeNameHeaderHtml(")) "header html helper"
Ok ($src.Contains("metrics-emp-surname")) "surname span class"
Ok ($src.Contains("metrics-emp-given")) "given span class"
Ok ($src.Contains("metricsEmployeeNameHeaderHtml(person)")) "cashiers use helper"
Ok ($src.Contains("metricsEmployeeNameHeaderHtml(employee)")) "shop/territory use helper"

Write-Host "=== CSS: wrap, no ellipsis on names ==="
$cssStart = $src.IndexOf(".metrics-emp-name {")
$cssSlice = $src.Substring($cssStart, [Math]::Min(900, $src.Length - $cssStart))
Ok ($cssSlice.Contains("white-space: normal")) "name allows wrap"
Ok ($cssSlice.Contains("overflow-wrap: anywhere") -or $cssSlice.Contains("word-break: break-word")) "long surname wrap"
Ok ($cssSlice.Contains("text-overflow: clip")) "no ellipsis on name"
Ok (-not ($cssSlice -match "text-overflow:\s*ellipsis")) "ellipsis removed from name rule"

Write-Host "=== Column widths unchanged ==="
Ok ($src.Contains("col.metrics-col-emp { width: 56px; }")) "default emp col 56"
Ok ($src.Contains("metrics-pickers-table col.metrics-col-emp { width: 52px; }")) "pickers emp 52"
Ok ($src.Contains("metrics-managers-table col.metrics-col-emp { width: 50px; }")) "managers emp 50"
Ok ($src.Contains("metrics-cashiers-table col.metrics-col-emp { width: 64px; }")) "cashiers emp 64"
Ok ($src.Contains("--metrics-date-w: 72px")) "date width kept"

Write-Host "=== Header height only ==="
Ok ($src.Contains("--metrics-h2: 58px")) "taller emp header row"

Write-Host "=== No data/API changes in helper ==="
$fn = $src.IndexOf("function metricsEmployeeNameHeaderHtml(")
$fnEnd = $src.IndexOf("`nfunction shopMetricEmployeeTitle(", $fn)
$block = $src.Substring($fn, [Math]::Min($fnEnd - $fn, 1200))
Ok ($block.IndexOf("saveApp") -lt 0) "helper does not save"
Ok ($block.IndexOf("employee.name =") -lt 0) "helper does not mutate name"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_metrics_emp_name_wrap.ps1 checks passed."
exit 0
