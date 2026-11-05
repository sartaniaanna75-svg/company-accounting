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
Ok ($html.Contains("!hasExplicitOps && prelimLeaves && prelimLeaves.length > 200")) "DomainWriter reject before dirty"
Ok ($html.Contains("const hasExplicitOps = !!(options.ops && options.ops.length)")) "hasExplicitOps boolean"
$explicitConstMatches = [regex]::Matches($html, 'const explicitOps\b')
Ok ($explicitConstMatches.Count -eq 1) "single const explicitOps (array)"
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
    Ok ($assignBlock.Contains("saveShopPayrollKpiAssignments(")) "toggle -> saveShopPayrollKpiAssignments"
    Ok ($assignBlock.Contains("prevAssignments")) "toggle keeps rollback snapshot"
    Ok ($assignBlock.Contains("ok === true")) "toggle success only on confirmed ok"
    Ok (-not [regex]::IsMatch($assignBlock, "(?<![A-Za-z_])saveApp\s*\(")) "toggle without saveApp"
}

# seed* больше не вызывает saveApp (полный collect при галочке KPI).
$seedW = $html.IndexOf("function seedShopWriteoffLossesKpi(")
$seedA = $html.IndexOf("function seedShopArrival730Kpi(")
$norm = $html.IndexOf("function normalizeSharedKpiScopes(")
Ok (($seedW -ge 0) -and ($seedA -gt $seedW) -and ($norm -gt $seedA)) "seed KPI helpers present"
if (($seedW -ge 0) -and ($seedA -gt $seedW) -and ($norm -gt $seedA)) {
    $seedWBlock = $html.Substring($seedW, $seedA - $seedW)
    $seedABlock = $html.Substring($seedA, $norm - $seedA)
    Ok (-not [regex]::IsMatch($seedWBlock, "(?<![A-Za-z_])saveApp\s*\(")) "seedWriteoff without saveApp"
    Ok (-not [regex]::IsMatch($seedABlock, "(?<![A-Za-z_])saveApp\s*\(")) "seedArrival730 without saveApp"
}

$assignSaveStart = $html.IndexOf("function saveShopPayrollKpiAssignments(")
$ensureStart = $html.IndexOf("function ensureShopPayroll(")
Ok (($assignSaveStart -ge 0) -and ($ensureStart -gt $assignSaveStart)) "assign saver before ensureShopPayroll"
if (($assignSaveStart -ge 0) -and ($ensureStart -gt $assignSaveStart)) {
    $assignSave = $html.Substring($assignSaveStart, $ensureStart - $assignSaveStart)
    Ok ($assignSave.Contains("forbidForcePut: true")) "assignments forbidForcePut"
    Ok ($assignSave.Contains('WT_SHOP_PAYROLL_KPI_ASSIGNMENTS_PATH')) "assignments path used"
    Ok (-not [regex]::IsMatch($assignSave, "(?<![A-Za-z_])saveApp\s*\(")) "assign saver without saveApp fallback"
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
Ok ($tests.Contains("test_kpi_assign_toggle_ignores_unrelated_dirty_collect")) "toggle ignores 496 unrelated dirty"

$harness = Join-Path $PSScriptRoot "wt_kpi_assign_save_harness.js"
Ok (Test-Path $harness) "assign save harness exists"
$nodeCandidates = @(
    (Get-Command node -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    "$env:LOCALAPPDATA\Programs\cursor\resources\app\resources\helpers\node.exe",
    "$env:LOCALAPPDATA\Programs\Cursor\resources\app\resources\helpers\node.exe",
    "$env:ProgramFiles\nodejs\node.exe"
) | Where-Object { $_ -and (Test-Path $_) }
$nodeExe = if ($nodeCandidates) { $nodeCandidates[0] } else { $null }
if ($nodeExe) {
    & $nodeExe $harness
    if ($LASTEXITCODE -ne 0) { throw "FAIL: wt_kpi_assign_save_harness.js" }
    Write-Host "  OK  wt_kpi_assign_save_harness.js"
} else {
    Write-Host "  SKIP  node not found for assign harness"
}

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_wt_kpi_narrow_ops.ps1 checks passed."
exit 0
