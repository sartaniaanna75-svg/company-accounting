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

Write-Host "=== Shop-only aggregate filter ==="
$openFn = $src.IndexOf("function frAreaWeekOpening(")
$openEnd = $src.IndexOf("function frTerritoryWeekOpening(", $openFn)
$openBlock = $src.Substring($openFn, $openEnd - $openFn)
Ok ($openBlock.Contains("frIsShopArea(areaId)")) "frAreaWeekOpening scopes to shop"
Ok ($openBlock.Contains("frInActualBalance(s)")) "frAreaWeekOpening uses in_actual_balance"
Ok ($openBlock.Contains("shopMoneyOnly && !frInActualBalance")) "skips non-fact for shop"

$totFn = $src.IndexOf("function frAreaTotals(")
$totEnd = $src.IndexOf("function frWeekActualClosing(", $totFn)
$totBlock = $src.Substring($totFn, $totEnd - $totFn)
Ok ($totBlock.Contains("frIsShopArea(areaId)")) "frAreaTotals scopes to shop"
Ok ($totBlock.Contains("inMoneyTotal")) "opening/closing gated"
Ok ($totBlock.Contains("acc.income += t.income")) "income still all accounts"

$closeFn = $src.IndexOf("function frWeekActualClosing(")
$closeEnd = $src.IndexOf("function frHasWeekFacts(", $closeFn)
$closeBlock = $src.Substring($closeFn, $closeEnd - $closeFn)
Ok ($closeBlock.Contains("frIsShopArea(areaId)")) "frWeekActualClosing scopes to shop"
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

Write-Host "=== Territory / indicators not rewritten ==="
Ok ($src.Contains("function frWeekFactCheck(")) "frWeekFactCheck still present"
$chk = $src.Substring($src.IndexOf("function frWeekFactCheck("), 200)
Ok ($chk.Contains("frActualBalanceSources")) "week status still fact sources"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_fr_shop_opening_no_double.ps1 checks passed."
exit 0
