# Metric extra employees (cross-dept). Run:
# powershell -NoProfile -ExecutionPolicy Bypass -File server/test_metric_extra_employees.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding $false
$src = [IO.File]::ReadAllText((Join-Path $Root "app.html"), $utf8)
$passed = 0; $failed = 0
function Ok($c, $m) {
    if ($c) { $script:passed++; Write-Host "  OK  $m" }
    else { $script:failed++; Write-Host "  FAIL  $m" -ForegroundColor Red }
}
function From-Utf8Bytes([byte[]]$bytes) { return $utf8.GetString($bytes) }

Write-Host "=== Markers ==="
Ok ($src.Contains("extra_employee_ids")) "extra_employee_ids field"
Ok ($src.Contains("function resolveUniqueEmployeeCard(")) "unique card resolver"
Ok ($src.Contains("function findEmployeeCardsById(")) "multi-card finder"
Ok ($src.Contains("function metricExtraEmployeesBlockHtml(")) "extra UI block"
Ok ($src.Contains("function addMetricExtraEmployee(")) "add handler"
Ok ($src.Contains("function removeMetricExtraEmployee(")) "remove handler"
Ok ($src.Contains("normalizeMetricExtraEmployeeIds")) "normalize extras"
Ok ($src.Contains("ambiguous")) "ambiguous id handling"
Ok ($src.Contains("function toggleMetricExtraPicker(")) "picker toggle"
Ok ($src.Contains("metricExtraHomeDeptsAllowed")) "home dept allowlist"

# UI strings via UTF-8 bytes (shop + territory variants)
Ok ($src.Contains((From-Utf8Bytes @(
    0xD0,0x94,0xD0,0xBE,0xD0,0xBF,0xD0,0xBE,0xD0,0xBB,0xD0,0xBD,0xD0,0xB8,0xD1,0x82,0xD0,0xB5,0xD0,0xBB,0xD1,0x8C,0xD0,0xBD,0xD1,0x8B,0xD0,0xB5,0x20,
    0xD1,0x81,0xD0,0xBE,0xD1,0x82,0xD1,0x80,0xD1,0x83,0xD0,0xB4,0xD0,0xBD,0xD0,0xB8,0xD0,0xBA,0xD0,0xB8,0x20,
    0xD0,0xB8,0xD0,0xB7,0x20,0xD0,0xB4,0xD1,0x80,0xD1,0x83,0xD0,0xB3,0xD0,0xB8,0xD1,0x85,0x20,
    0xD1,0x80,0xD0,0xB0,0xD0,0xB1,0xD0,0xBE,0xD1,0x87,0xD0,0xB8,0xD1,0x85,0x20,
    0xD0,0xB3,0xD1,0x80,0xD1,0x83,0xD0,0xBF,0xD0,0xBF
)))) "shop UI title utf8"
Ok ($src.Contains((From-Utf8Bytes @(
    0xD0,0x94,0xD0,0xBE,0xD0,0xBF,0xD0,0xBE,0xD0,0xBB,0xD0,0xBD,0xD0,0xB8,0xD1,0x82,0xD0,0xB5,0xD0,0xBB,0xD1,0x8C,0xD0,0xBD,0xD1,0x8B,0xD0,0xB5,0x20,
    0xD1,0x81,0xD0,0xBE,0xD1,0x82,0xD1,0x80,0xD1,0x83,0xD0,0xB4,0xD0,0xBD,0xD0,0xB8,0xD0,0xBA,0xD0,0xB8,0x20,
    0xD0,0xB8,0xD0,0xB7,0x20,0xD0,0xB4,0xD1,0x80,0xD1,0x83,0xD0,0xB3,0xD0,0xB8,0xD1,0x85,0x20,
    0xD0,0xBF,0xD0,0xBE,0xD0,0xB4,0xD1,0x80,0xD0,0xB0,0xD0,0xB7,0xD0,0xB4,0xD0,0xB5,0xD0,0xBB,0xD0,0xB5,0xD0,0xBD,0xD0,0xB8,0xD0,0xB9
)))) "territory UI title utf8"
Ok ($src.Contains((From-Utf8Bytes @(
    0xD0,0xB4,0xD1,0x80,0xD1,0x83,0xD0,0xB3,0xD0,0xBE,0xD0,0xB9,0x20,
    0xD1,0x80,0xD0,0xB0,0xD0,0xB1,0xD0,0xBE,0xD1,0x87,0xD0,0xB5,0xD0,0xB9,0x20,
    0xD0,0xB3,0xD1,0x80,0xD1,0x83,0xD0,0xBF,0xD0,0xBF,0xD1,0x8B
)))) "shop add button utf8"
Ok ($src.Contains((From-Utf8Bytes @(
    0xD0,0xB4,0xD1,0x80,0xD1,0x83,0xD0,0xB3,0xD0,0xBE,0xD0,0xB3,0xD0,0xBE,0x20,
    0xD0,0xBF,0xD0,0xBE,0xD0,0xB4,0xD1,0x80,0xD0,0xB0,0xD0,0xB7,0xD0,0xB4,0xD0,0xB5,0xD0,0xBB,0xD0,0xB5,0xD0,0xBD,0xD0,0xB8,0xD1,0x8F
)))) "territory add button utf8"

$peopleFn = $src.IndexOf("function peopleAssignedToMetricOnTab(")
$peopleSlice = $src.Substring($peopleFn, [Math]::Min(2200, $src.Length - $peopleFn))
Ok ($peopleSlice.Contains("extra_employee_ids")) "work table includes extras"
Ok ($peopleSlice.Contains("resolveUniqueEmployeeCard")) "work table uses unique resolve"
Ok ($peopleSlice.Contains('deptCode === "shop"') -or $peopleSlice.Contains("deptCode === 'shop'")) "territory people helper still excludes shop pool path"

$matchFn = $src.IndexOf("function employeeMatchesMetricAssignments(")
$matchSlice = $src.Substring($matchFn, [Math]::Min(2800, $src.Length - $matchFn))
Ok ($matchSlice.Contains("extra_employee_ids")) "match includes extras"

$addFn = $src.IndexOf("function addMetricExtraEmployee(")
$addSlice = $src.Substring($addFn, [Math]::Min(2800, $src.Length - $addFn))
Ok ($addSlice.Contains("ambiguous")) "add blocks ambiguous id"

$allowFn = $src.IndexOf("function metricExtraHomeDeptsAllowed(")
$allowSlice = $src.Substring($allowFn, [Math]::Min(500, $src.Length - $allowFn))
Ok ($allowSlice.Contains('return ["shop"]') -or $allowSlice.Contains("return ['shop']")) "shop allowlist returns shop"
Ok ($allowSlice.Contains("TERRITORY_DEPT_CODES.filter")) "territory allowlist unchanged"

Ok ($src.Contains("function shopEmployeeIsMetricExtraOnTab(")) "shop extra-on-tab helper"
$wmp = $src.IndexOf("function workMetricPeopleForTab(")
$wmpSlice = $src.Substring($wmp, [Math]::Min(3200, $src.Length - $wmp))
Ok ($wmpSlice.Contains("shopEmployeeIsMetricExtraOnTab")) "shop people uses extra exception"
Ok ($wmpSlice.Contains('code !== "shop" && metricItem') -or $wmpSlice.Contains("code !== 'shop' && metricItem")) "territory assignment branch kept"

$extraStart = $src.IndexOf("function findEmployeeCardsById(")
$extraEnd = $src.IndexOf("`nfunction ensureMetricAssignments(")
if ($extraEnd -lt 0) { $extraEnd = $extraStart + 6000 }
$extraBlock = $src.Substring($extraStart, [Math]::Min($extraEnd - $extraStart, $src.Length - $extraStart))
Ok ($extraBlock.IndexOf("shopPayroll") -lt 0) "no payroll rewrite in extra helpers"
Ok ($extraBlock.IndexOf("migrateAppData") -lt 0) "no migrate in extra helpers"
Ok ($src.Contains("normalizeMetricAssignmentRow") -and $src.Contains("extra_employee_ids: extras")) "normalize keeps extras"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_metric_extra_employees.ps1 checks passed."
exit 0
