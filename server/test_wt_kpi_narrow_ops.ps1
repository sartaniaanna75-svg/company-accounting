# Markers: narrow KPI OPS save + DomainWriter >200 reject before dirty.
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File server/test_wt_kpi_narrow_ops.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding $false
$html = [IO.File]::ReadAllText((Join-Path $Root "app.html"), $utf8)
$api = [IO.File]::ReadAllText((Join-Path $Root "server\wt_api.py"), $utf8)
$tests = [IO.File]::ReadAllText((Join-Path $Root "server\test_wt_api.py"), $utf8)
$passed = 0; $failed = 0
function Ok($c, $m) {
    if ($c) { $script:passed++; Write-Host "  OK  $m" }
    else { $script:failed++; Write-Host "  FAIL  $m" -ForegroundColor Red }
}

Write-Host "=== Client narrow KPI OPS ==="
Ok ($html.Contains("function saveShopPayrollKpiDefs(")) "saveShopPayrollKpiDefs"
Ok ($html.Contains("function saveShopPayrollKpiAssignments(")) "saveShopPayrollKpiAssignments"
Ok ($html.Contains('WT_SHOP_PAYROLL_KPI_DEFS_PATH = "shopPayroll.kpiDefs"')) "kpiDefs path const"
Ok ($html.Contains('WT_SHOP_PAYROLL_KPI_ASSIGNMENTS_PATH = "shopPayroll.assignments"')) "assignments path const"
Ok ($html.Contains("!explicitOps && prelimLeaves && prelimLeaves.length > 200")) "DomainWriter reject before dirty"
Ok ($html.Contains("explicitOps")) "explicitOps flag in DomainWriter"
Ok ($html.Contains("isKpiMoneyPath")) "KPI conflict path handling"

$createStart = $html.IndexOf("function createShopKpiDef(")
$toggleStart = $html.IndexOf("function toggleShopKpiAssignment(")
Ok (($createStart -ge 0) -and ($toggleStart -gt $createStart)) "create/toggle markers present"
if (($createStart -ge 0) -and ($toggleStart -gt $createStart)) {
    $createBlock = $html.Substring($createStart, $toggleStart - $createStart)
    Ok ($createBlock.Contains("saveShopPayrollKpiDefs()")) "createShopKpiDef -> saveShopPayrollKpiDefs"
    Ok (-not [regex]::IsMatch($createBlock, "(?<![A-Za-z_])saveApp\s*\(")) "createShopKpiDef without saveApp"
    $assignEnd = $html.IndexOf("function addShopKpi(")
    $assignBlock = $html.Substring($toggleStart, [Math]::Max(0, $assignEnd - $toggleStart))
    Ok ($assignBlock.Contains("saveShopPayrollKpiAssignments()")) "toggle -> saveShopPayrollKpiAssignments"
    Ok (-not [regex]::IsMatch($assignBlock, "(?<![A-Za-z_])saveApp\s*\(")) "toggle without saveApp"
}

$mutateStart = $html.IndexOf("function mutateShopKpiDef(")
$setFieldStart = $html.IndexOf("function setShopKpiDefField(")
if (($mutateStart -ge 0) -and ($setFieldStart -gt $mutateStart)) {
    $mutateBlock = $html.Substring($mutateStart, $setFieldStart - $mutateStart)
    Ok ($mutateBlock.Contains("saveShopPayrollKpiDefs()")) "mutateShopKpiDef -> narrow save"
}

$closeStart = $html.IndexOf("function closeShopKpiDef(")
if (($closeStart -ge 0) -and ($createStart -gt $closeStart)) {
    $closeBlock = $html.Substring($closeStart, $createStart - $closeStart)
    Ok ($closeBlock.Contains("saveShopPayrollKpiDefs()")) "closeShopKpiDef -> narrow save"
}

Write-Host "=== Server limits unchanged ==="
Ok ($api.Contains("if len(ops) > 200:")) "server batch cap 200"
Ok ($api.Contains('"error": "too many ops"')) "too many ops error"
Ok ($api.Contains("SHOP_PAYROLL_KPI_MONEY_KEYS")) "KPI money keys ACL"

Write-Host "=== Automated tests present ==="
Ok ($tests.Contains("class WtShopPayrollKpiOpsTests")) "WtShopPayrollKpiOpsTests"
Ok ($tests.Contains("test_admin_creates_two_kpi_defs_via_ops")) "create two KPI"
Ok ($tests.Contains("test_kpi_save_succeeds_despite_large_unrelated_facts")) "large unrelated diff"
Ok ($tests.Contains("test_kpi_conflict_two_editors_no_overwrite")) "conflict two editors"
Ok ($tests.Contains("test_kpi_persists_after_get_reload")) "reload persistence"
Ok ($tests.Contains("test_batch_too_many_ops_still_capped_at_200")) "limit 200 kept"
Ok ($tests.Contains("test_kpi_assignment_ops")) "assignment ops"
Ok ($tests.Contains("test_kpi_mutate_and_close_via_ops")) "mutate and close"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_wt_kpi_narrow_ops.ps1 checks passed."
exit 0
