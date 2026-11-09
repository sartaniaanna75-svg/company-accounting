# Markers: summary salary rounding isolated to «Общее → Зарплата».
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$App = Get-Content -LiteralPath (Join-Path $Root "app.html") -Raw -Encoding UTF8

function Ok([bool]$Cond, [string]$Name) {
    if (-not $Cond) { throw "FAIL $Name" }
    Write-Host "OK $Name"
}

Ok ($App -match "SUMMARY_SALARY_ROUND_FROM\s*=\s*`"2026-10`"") "round-from constant"
Ok ($App -match "function summarySalaryRoundRub") "round helper"
Ok ($App -match "function summarySalaryAppliesRound") "applies helper"
Ok ($App -match "function applySummarySalaryTotalsRounding") "apply helper"
Ok ($App -match "applySummarySalaryTotalsRounding\(calculateEmployee\(employee\.id\),\s*selectedMonth\)") "salaryRow uses apply"
# Только salaryRow вызывает apply с calculateEmployee — ведомости отделов не должны.
$applyHits = [regex]::Matches($App, "applySummarySalaryTotalsRounding\(").Count
Ok ($applyHits -eq 2) "apply defined once + salaryRow once (2 hits)"

# Ведомости отделов по-прежнему берут сырой toHand и money() на отображении.
Ok ($App -match "total:\s*data\.toHand") "dept/TP total still toHand"
$shopIdx = $App.IndexOf("function shopSalaryRowData")
Ok ($shopIdx -ge 0) "shopSalaryRowData present"
$shopSlice = $App.Substring($shopIdx, [Math]::Min(800, $App.Length - $shopIdx))
Ok ($shopSlice -notmatch "applySummarySalaryTotalsRounding") "shopSalaryRowData no summary round"

$payIdx = $App.IndexOf("function renderPayrollView")
$paySlice = $App.Substring($payIdx, [Math]::Min(6000, $App.Length - $payIdx))
Ok ($paySlice -match "money\(row\.total\)") "payroll view money(total)"
Ok ($paySlice -notmatch "applySummarySalaryTotalsRounding") "payroll view no summary round"

# calculateEmployee сам не округляет четыре итога.
$calcIdx = $App.IndexOf("function calculateEmployee")
$calcEnd = $App.IndexOf("function departmentMonthTotals", $calcIdx)
Ok (($calcIdx -ge 0) -and ($calcEnd -gt $calcIdx)) "calculateEmployee span"
$calcSlice = $App.Substring($calcIdx, $calcEnd - $calcIdx)
Ok ($calcSlice -notmatch "summarySalaryRoundRub") "calculateEmployee no summary round"
Ok ($calcSlice -match "const accruedTotal = pay \+ products \+ advance") "accruedTotal formula intact"
Ok ($calcSlice -match "reflect1c:\s*accruedTotal - advance") "reflect1c formula intact"

Write-Host "OK test_wt_summary_salary_round_markers"
