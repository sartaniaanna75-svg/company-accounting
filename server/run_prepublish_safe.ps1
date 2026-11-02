# WorkTime prepublish safe suite.
# FakeConn / file markers / JS parse only. No live HTTP, no PostgreSQL.
# Run:
#   powershell -NoProfile -ExecutionPolicy Bypass -File server/run_prepublish_safe.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $Root "server"
$Py = Join-Path $Root ".tools\python-embed\python.exe"

$script:Results = New-Object System.Collections.Generic.List[object]
$script:Failed = 0
$script:Skipped = 0

function Add-Result([string]$Name, [string]$Status, [string]$Detail = "") {
    $script:Results.Add([pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail })
    if ($Status -eq "FAIL") { $script:Failed++ }
    if ($Status -eq "SKIP") { $script:Skipped++ }
}

function Test-ForbiddenUrl([string]$Value, [string]$Label) {
    $v = ($Value + "").Trim()
    if (-not $v) { return $null }
    $low = $v.ToLowerInvariant()
    $markers = @(
        "timeweb", "twcstorage", "amazonaws", "azure", "supabase", "neon.tech",
        "railway", "render.com", "digitalocean", "sartania-systems", "worktime.sartania"
    )
    foreach ($m in $markers) {
        if ($low.Contains($m)) {
            return "$Label : forbidden marker '$m' (possible production access)."
        }
    }
    if ($low -match 'https?://([^/:]+)') {
        $hostName = $Matches[1]
        if ($hostName -ne "127.0.0.1" -and $hostName -ne "localhost") {
            return "$Label : external host '$hostName'."
        }
    }
    if ($low -match '@([^/:]+):') {
        $dbHost = $Matches[1]
        if ($dbHost -ne "127.0.0.1" -and $dbHost -ne "localhost") {
            return "$Label : external DB host '$dbHost'."
        }
    }
    return $null
}

Write-Host "=== WorkTime prepublish safe suite ===" -ForegroundColor Cyan
Write-Host "Root: $Root"
Write-Host "Otchet RU: sintaksis JS, FakeConn OPS/KPI, markery izoljacii, SoftSync FakeConn."
Write-Host ""

$envProblems = @()
foreach ($pair in @(
        @{ N = "DATABASE_URL"; V = $env:DATABASE_URL },
        @{ N = "WT_TEST_DATABASE_URL"; V = $env:WT_TEST_DATABASE_URL },
        @{ N = "WT_LIVE_BASE"; V = $env:WT_LIVE_BASE },
        @{ N = "WT_API_BASE"; V = $env:WT_API_BASE }
    )) {
    $err = Test-ForbiddenUrl $pair.V $pair.N
    if ($err) { $envProblems += $err }
}
if ($env:WT_LIVE_BASE -and ($env:WT_LIVE_BASE + "").Trim()) {
    $lb = ($env:WT_LIVE_BASE + "").Trim().ToLowerInvariant()
    if ($lb -notmatch '^https?://(127\.0\.0\.1|localhost)(:\d+)?/?') {
        $envProblems += "WT_LIVE_BASE is set and is not localhost; live runs forbidden."
    }
}

if ($envProblems.Count -gt 0) {
    Write-Host "STOP: launch safety not confirmed. Tests were NOT started." -ForegroundColor Red
    foreach ($p in $envProblems) { Write-Host "  - $p" -ForegroundColor Red }
    Write-Host "Clear/fix environment variables and retry."
    exit 2
}

$prevLive = $env:WT_LIVE_BASE
$prevTestDb = $env:WT_TEST_DATABASE_URL
Remove-Item Env:WT_LIVE_BASE -ErrorAction SilentlyContinue
Remove-Item Env:WT_TEST_DATABASE_URL -ErrorAction SilentlyContinue

function Invoke-Step([string]$Name, [scriptblock]$Action) {
    Write-Host "--- $Name ---"
    try {
        $global:LASTEXITCODE = 0
        & $Action
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        if ($code -ne 0) {
            Add-Result $Name "FAIL" "exit=$code"
            Write-Host "FAIL $Name (exit $code)" -ForegroundColor Red
            Write-Host ""
            return
        }
        Add-Result $Name "OK" ""
        Write-Host "OK  $Name" -ForegroundColor Green
    } catch {
        Add-Result $Name "FAIL" $_.Exception.Message
        Write-Host "FAIL $Name : $($_.Exception.Message)" -ForegroundColor Red
    }
    Write-Host ""
}

try {
    if (-not (Test-Path (Join-Path $ServerDir "check_app_html_syntax.ps1"))) {
        Write-Host "STOP: check_app_html_syntax.ps1 not found" -ForegroundColor Red
        exit 2
    }
    if (-not (Test-Path $Py)) {
        Write-Host "STOP: portable Python missing: $Py" -ForegroundColor Red
        exit 2
    }

    Invoke-Step "Syntax app.html (check_app_html_syntax.ps1)" {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ServerDir "check_app_html_syntax.ps1")
    }
    # test_wt_ops_foundation.ps1 NOT run: stale marker expects localStorage wtOps==="1"
    # while app uses health.ops / wtOps==="0". Do not edit that test here.
    # Real OPS coverage: FakeConn WtOpsFoundationTests below.
    Add-Result "OPS foundation markers (test_wt_ops_foundation.ps1)" "SKIP" "Stale UI-flag marker; covered by FakeConn WtOpsFoundationTests; existing test not modified"

    Invoke-Step "KPI OPS markers (test_wt_kpi_narrow_ops.ps1)" {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ServerDir "test_wt_kpi_narrow_ops.ps1")
    }
    Invoke-Step "Shop metrics markers (test_shop_metric_participants.ps1)" {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ServerDir "test_shop_metric_participants.ps1")
    }
    Invoke-Step "Metric extra employees markers (test_metric_extra_employees.ps1)" {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ServerDir "test_metric_extra_employees.ps1")
    }

    Push-Location $Root
    try {
        Invoke-Step "FakeConn OPS foundation (WtOpsFoundationTests)" {
            & $Py -m unittest discover -s server -p test_wt_api.py -k WtOpsFoundationTests -v
        }
        Invoke-Step "FakeConn OPS integration (WtOpsIntegrationTests)" {
            & $Py -m unittest discover -s server -p test_wt_api.py -k WtOpsIntegrationTests -v
        }
        Invoke-Step "FakeConn KPI OPS (WtShopPayrollKpiOpsTests)" {
            & $Py -m unittest discover -s server -p test_wt_api.py -k WtShopPayrollKpiOpsTests -v
        }
        Invoke-Step "Shop/territory isolation (shop_territory_assignment_isolation)" {
            & $Py -m unittest discover -s server -p test_wt_api.py -k shop_territory_assignment_isolation -v
        }
        Invoke-Step "Territory fact dept (test_metric_fact_dept.py)" {
            & $Py -m unittest discover -s server -p test_metric_fact_dept.py -v
        }
        Invoke-Step "SoftSync FakeConn + wiring (test_wt_soft_refresh.py)" {
            & $Py -m unittest discover -s server -p test_wt_soft_refresh.py -v
        }
    } finally {
        Pop-Location
    }

    Add-Result "PostgreSQL OPS (test_wt_ops_pg)" "SKIP" "Needs local WT_TEST_DATABASE_URL; not in safe suite"
    Add-Result "Live timesheet OPS (test_wt_timesheet_op_live)" "SKIP" "Writes to a server; forbidden"
    Add-Result "Browser PIN / timesheet / F5 UI" "SKIP" "No UI smoke in this suite"

} finally {
    if ($null -ne $prevLive) { $env:WT_LIVE_BASE = $prevLive }
    if ($null -ne $prevTestDb) { $env:WT_TEST_DATABASE_URL = $prevTestDb }
}

Write-Host ""
Write-Host "=== Otchet (prepublish safe) ===" -ForegroundColor Cyan
Write-Host "Proshli / ne proshli / ne zapuskalis:"
foreach ($r in $script:Results) {
    $color = "White"
    if ($r.Status -eq "OK") { $color = "Green" }
    elseif ($r.Status -eq "FAIL") { $color = "Red" }
    elseif ($r.Status -eq "SKIP") { $color = "Yellow" }
    $line = ("[{0}] {1}" -f $r.Status, $r.Name)
    if ($r.Detail) { $line += " - " + $r.Detail }
    Write-Host $line -ForegroundColor $color
}

$okCount = @($script:Results | Where-Object { $_.Status -eq "OK" }).Count
Write-Host ""
Write-Host ("Itogo: OK={0} FAIL={1} SKIP={2}" -f $okCount, $script:Failed, $script:Skipped)
Write-Host "Bezopasnost: net live HTTP k rabochemu serveru, net PostgreSQL (tolko FakeConn + faily + parse)."

if ($script:Failed -gt 0) {
    Write-Host "Rezultat: NE PROJDENO - publikaciju vypolnjat nelzya." -ForegroundColor Red
    exit 1
}
Write-Host "Rezultat: PROJDENO - bezopasnyj nabor zelenyj." -ForegroundColor Green
exit 0
