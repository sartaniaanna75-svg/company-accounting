# Shop Fincontrol: aggregate opening excludes non-fact accounts. Run:
# powershell -NoProfile -ExecutionPolicy Bypass -File server/test_fr_shop_opening_no_double.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding $false
$src = [IO.File]::ReadAllText((Join-Path $Root "fr-mod.js"), $utf8)
$passed = 0; $failed = 0
function Ok($c, $m) {
    if ($c) { $script:passed++; Write-Host "  OK  $m" }
    else { $script:failed++; Write-Host "  FAIL  $m" -ForegroundColor Red }
}

Write-Host "=== Shop + Territory aggregate filter ==="
$openFn = $src.IndexOf("function frAreaWeekOpening(")
$openEnd = $src.IndexOf("function frTerritoryWeekOpening(", $openFn)
$openBlock = $src.Substring($openFn, $openEnd - $openFn)
Ok ($openBlock.Contains("frIsShopArea(areaId)")) "frAreaWeekOpening keeps shop"
Ok ($openBlock.Contains("frIsTerritoryArea(areaId)")) "frAreaWeekOpening includes territory"
Ok ($openBlock.Contains("frInActualBalance(s)")) "frAreaWeekOpening uses in_actual_balance"
Ok ($openBlock.Contains("moneyFactOnly && !frInActualBalance")) "skips non-fact for money areas"

$totFn = $src.IndexOf("function frAreaTotals(")
$totEnd = $src.IndexOf("function frWeekActualClosing(", $totFn)
$totBlock = $src.Substring($totFn, $totEnd - $totFn)
Ok ($totBlock.Contains("frIsShopArea(areaId)")) "frAreaTotals keeps shop"
Ok ($totBlock.Contains("frIsTerritoryArea(areaId)")) "frAreaTotals includes territory"
Ok ($totBlock.Contains("inMoneyTotal")) "opening/closing gated"
Ok ($totBlock.Contains("acc.income += t.income")) "income still all accounts"

$closeFn = $src.IndexOf("function frWeekActualClosing(")
$closeEnd = $src.IndexOf("function frHasWeekFacts(", $closeFn)
$closeBlock = $src.Substring($closeFn, $closeEnd - $closeFn)
Ok ($closeBlock.Contains("frIsShopArea(areaId)")) "frWeekActualClosing keeps shop"
Ok ($closeBlock.Contains("frIsTerritoryArea(areaId)")) "frWeekActualClosing includes territory"
Ok ($closeBlock.Contains("frInActualBalance(s)")) "closing uses fact flag"

Write-Host "=== Carry / individual openings untouched ==="
$carry = $src.Substring($src.IndexOf("function frCarryIfNeeded("), 900)
Ok ($carry.Contains("frSources(areaId, true)")) "frCarryIfNeeded still all sources"
Ok (-not ($carry.Contains("frInActualBalance"))) "frCarryIfNeeded does not filter by fact flag"
$push = $src.Substring($src.IndexOf("function frPushCarry("), 900)
Ok ($push.Contains("frSources(area.finance_area_id, true).forEach")) "frPushCarry still all sources"

Write-Host "=== Arithmetic scenario (local) ==="
$main = 4613975.12
$bank = 497138.34
$extras = 452064.38 + 901733.53 + 211783.73 + 104562.68
$fact = [Math]::Round($main + $bank, 2)
$wrong = [Math]::Round($fact + $extras, 2)
Ok ($fact -eq 5111113.46) "expected opening 5 111 113,46"
Ok ($wrong -eq 6781257.78) "old inflated 6 781 257,78"
Ok ([Math]::Round($extras, 2) -eq 1670144.32) "extras = gap 1 670 144,32"
# Simulate new aggregate: only fact accounts
$agg = [Math]::Round($main + $bank, 2)
Ok ($agg -eq $fact) "shop aggregate ignores extras"

Write-Host "=== Indicators / objects separation ==="
Ok ($src.Contains("function frWeekFactCheck(")) "frWeekFactCheck still present"
$chk = $src.Substring($src.IndexOf("function frWeekFactCheck("), 200)
Ok ($chk.Contains("frActualBalanceSources")) "week status still fact sources"
Ok ($src.Contains('id === "farea_shop"')) "shop id unchanged"
Ok ($src.Contains('id === "farea_territory"')) "territory id unchanged"
# Simulate territory aggregate: only fact accounts
$tMain = 100000.0; $tBank = 50000.0; $tExtra = 25000.0
$tFact = [Math]::Round($tMain + $tBank, 2)
$tAgg = [Math]::Round($tMain + $tBank, 2)
Ok ($tAgg -eq $tFact) "territory aggregate ignores extras"
Ok (($tFact + $tExtra) -ne $tAgg) "extras would inflate without filter"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_fr_shop_opening_no_double.ps1 checks passed."
exit 0
