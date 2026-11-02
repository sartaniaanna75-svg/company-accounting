# Shop metric participants / month totals (Volodarsky). Run:
# powershell -NoProfile -ExecutionPolicy Bypass -File server/test_shop_metric_participants.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding $false
$src = [IO.File]::ReadAllText((Join-Path $Root "app.html"), $utf8)
$passed = 0; $failed = 0
function Ok($c, $m) {
    if ($c) { $script:passed++; Write-Host "  OK  $m" }
    else { $script:failed++; Write-Host "  FAIL  $m" -ForegroundColor Red }
}

Write-Host "=== Shop participant markers ==="
Ok ($src.Contains("function shopPeopleAssignedToMetricOnTab(")) "shopPeopleAssignedToMetricOnTab exists"
Ok ($src.Contains("function shopMetricParticipantPeople(")) "shopMetricParticipantPeople exists"
Ok ($src.Contains("function shopRecountDayExecutorTotal(")) "recount total helper kept"

$wmp = $src.IndexOf("function workMetricPeopleForTab(")
$wmpEnd = $src.IndexOf("`nfunction deptMetricFields(", $wmp)
if ($wmpEnd -lt 0) { $wmpEnd = $wmp + 5000 }
$wmpSlice = $src.Substring($wmp, [Math]::Min($wmpEnd - $wmp, $src.Length - $wmp))
Ok ($wmpSlice.Contains("shopPeopleAssignedToMetricOnTab(metricItem, tab, mid)")) "shop branch calls assignment people"
Ok ($wmpSlice.Contains("peopleAssignedToMetricOnTab")) "territory branch kept"

$shopFn = $src.IndexOf("function shopPeopleAssignedToMetricOnTab(")
$shopEnd = $src.IndexOf("`nfunction employeeMatchesMetricAssignments(", $shopFn)
if ($shopEnd -lt 0) { $shopEnd = $shopFn + 3500 }
$shopSlice = $src.Substring($shopFn, [Math]::Min($shopEnd - $shopFn, $src.Length - $shopFn))
Ok ($shopSlice.Contains("extra_employee_ids")) "includes extras"
Ok ($shopSlice.Contains("employee_ids")) "includes employee_ids"
Ok ($shopSlice.Contains("shopEmployeeHasWorkMetric")) "requires existing workAccess"
Ok ($shopSlice.Contains("resolveUniqueEmployeeCard")) "unique card resolve"
Ok ($shopSlice.Contains("employeeHomeDeptCode")) "home dept check"
Ok ($shopSlice.IndexOf("applyShopEmployeeWorkMetric") -lt 0) "does not grant workAccess"

$partFn = $src.IndexOf("function shopMetricParticipantPeople(")
$partEnd = $src.IndexOf("`nfunction shopMetricStoreMonthTotal(", $partFn)
if ($partEnd -lt 0) { $partEnd = $partFn + 2500 }
$partSlice = $src.Substring($partFn, [Math]::Min($partEnd - $partFn, $src.Length - $partFn))
Ok ($partSlice.Contains("workMetricPeopleForTab")) "participants use people-for-tab"
Ok ($partSlice.Contains("prevMonth")) "month swap for participants"

$totFn = $src.IndexOf("function shopMetricStoreMonthTotal(")
$totEnd = $src.IndexOf("`nfunction getShopMetricValue(", $totFn)
if ($totEnd -lt 0) { $totEnd = $totFn + 2000 }
$totSlice = $src.Substring($totFn, [Math]::Min($totEnd - $totFn, $src.Length - $totFn))
Ok ($totSlice.Contains("shopMetricParticipantPeople")) "month total uses participants"
Ok ($totSlice.Contains('mid === "metric_recount"') -or $totSlice.Contains("mid === 'metric_recount'")) "recount special path kept"
Ok ($totSlice.IndexOf("shopMetricContributorIds") -lt 0) "month total ignores contributor orphans"

$recFn = $src.IndexOf("function shopRecountDayExecutorTotal(")
$recEnd = $src.IndexOf("`nfunction setShopRecountResponsible(", $recFn)
$recSlice = $src.Substring($recFn, [Math]::Min($recEnd - $recFn, $src.Length - $recFn))
Ok ($recSlice.Contains('workMetricPeopleForTab("shop", "metric_recount", "pickers")')) "recount uses pickers people"
Ok ($recSlice.IndexOf("shopRecountExecutorIds") -lt 0) "recount no longer sums executor orphans"

# Isolation markers: shop people helper must not pull territory pools
Ok ($shopSlice.IndexOf("TERRITORY_DEPT_CODES") -lt 0) "shop people helper no territory pool"
Ok ($shopSlice.Contains('!== "shop"') -or $shopSlice.Contains("!== 'shop'")) "rejects non-shop cards"

# SPU left on contributor path for non-recount (no silent KPI/SPU rewrite)
$spu = $src.IndexOf("function collectShopMetricPeriodSeries(")
$spuSlice = $src.Substring($spu, [Math]::Min(1200, $src.Length - $spu))
Ok ($spuSlice.Contains("shopMetricContributorIds")) "SPU non-recount still uses contributorIds (unchanged)"
Ok ($spuSlice.Contains("shopRecountDayExecutorTotal")) "SPU recount uses fixed day total"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_shop_metric_participants.ps1 checks passed."
exit 0
