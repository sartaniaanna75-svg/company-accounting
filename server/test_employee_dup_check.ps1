# Employee duplicate check UI (read-only). Run:
# powershell -NoProfile -ExecutionPolicy Bypass -File server/test_employee_dup_check.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$AppHtml = Join-Path $Root "app.html"
$utf8 = New-Object System.Text.UTF8Encoding $false
$src = [System.IO.File]::ReadAllText($AppHtml, $utf8)

$script:passed = 0
$script:failed = 0

function Assert-True {
    param([bool]$Cond, [string]$Msg)
    if ($Cond) {
        $script:passed++
        Write-Host ("  OK  " + $Msg)
    } else {
        $script:failed++
        Write-Host ("  FAIL  " + $Msg) -ForegroundColor Red
    }
}

function Assert-Contains {
    param([string]$Hay, [string]$Needle, [string]$Msg)
    Assert-True ($Hay.IndexOf($Needle) -ge 0) $Msg
}

function Assert-NotContains {
    param([string]$Hay, [string]$Needle, [string]$Msg)
    Assert-True ($Hay.IndexOf($Needle) -lt 0) $Msg
}

function From-Utf8Bytes([byte[]]$bytes) {
    return $utf8.GetString($bytes)
}

Write-Host "=== Source markers ==="
Assert-Contains $src 'id="employeeDupCheckBtn"' "dup check button"
Assert-Contains $src "employeeDupCheckBtnWrap" "button wrap near search"
Assert-Contains $src "function openEmployeeDupCheck(" "open handler"
Assert-Contains $src "function buildEmployeeDuplicateReport(" "report builder"
Assert-Contains $src "function collectEmployeeDupScanEntries(" "scan collector"
Assert-Contains $src "function normalizeEmployeeNameForDupCheck(" "name normalize"
Assert-Contains $src "function loadPayloadForEmployeeDupCheck(" "payload loader"
Assert-Contains $src 'method: "GET"' "uses GET for state"
Assert-Contains $src "employeeDupCheckTitle" "modal title id"
Assert-Contains $src "emp-dup-msg err" "server error style"
Assert-Contains $src "emp-dup-msg ok" "clean result style"
Assert-Contains $src "isAppAdmin()" "admin gate"
Assert-Contains $src "syncEmployeeDupCheckButton" "button visibility sync"
Assert-Contains $src "groupCount" "group size in table rows"
Assert-Contains $src 'id="employeeDupCheckBtnWrap" style="display:none;"' "button hidden by default"

# Russian UI via UTF-8 bytes (avoids ps1 source encoding issues)
Assert-Contains $src (From-Utf8Bytes @(
    0xD0,0x9F,0xD1,0x80,0xD0,0xBE,0xD0,0xB2,0xD0,0xB5,0xD1,0x80,0xD0,0xB8,0xD1,0x82,0xD1,0x8C,0x20,
    0xD0,0xB4,0xD1,0x83,0xD0,0xB1,0xD0,0xBB,0xD0,0xB8
)) "button label utf8"
Assert-Contains $src (From-Utf8Bytes @(
    0xD0,0x9D,0xD0,0xB5,0x20,0xD1,0x83,0xD0,0xB4,0xD0,0xB0,0xD0,0xBB,0xD0,0xBE,0xD1,0x81,0xD1,0x8C,0x20,
    0xD0,0xBF,0xD0,0xBE,0xD0,0xBB,0xD1,0x83,0xD1,0x87,0xD0,0xB8,0xD1,0x82,0xD1,0x8C,0x20,
    0xD0,0xB0,0xD0,0xBA,0xD1,0x82,0xD1,0x83,0xD0,0xB0,0xD0,0xBB,0xD1,0x8C,0xD0,0xBD,0xD1,0x8B,0xD0,0xB5,0x20,
    0xD0,0xB4,0xD0,0xB0,0xD0,0xBD,0xD0,0xBD,0xD1,0x8B,0xD0,0xB5
)) "server error message utf8"
Assert-Contains $src (From-Utf8Bytes @(
    0xD0,0x9E,0xD0,0xB1,0xD0,0xBD,0xD0,0xB0,0xD1,0x80,0xD1,0x83,0xD0,0xB6,0xD0,0xB5,0xD0,0xBD,0xD1,0x8B,0x20,
    0xD1,0x81,0xD0,0xBE,0xD0,0xB2,0xD0,0xBF,0xD0,0xB0,0xD0,0xB4,0xD0,0xB5,0xD0,0xBD,0xD0,0xB8,0xD1,0x8F
)) "found message utf8"
Assert-Contains $src (From-Utf8Bytes @(
    0xD0,0x9F,0xD0,0xBE,0xD0,0xB2,0xD1,0x82,0xD0,0xBE,0xD1,0x80,0xD1,0x8F,0xD1,0x8E,0xD1,0x89,0xD0,0xB8,0xD1,0x85,0xD1,0x81,0xD1,0x8F,0x20,
    0x49,0x44,0x20,0xD0,0xB8,0x20,0xD0,0xA4,0xD0,0x98,0xD0,0x9E,0x20,
    0xD0,0xBD,0xD0,0xB5,0x20,0xD0,0xBE,0xD0,0xB1,0xD0,0xBD,0xD0,0xB0,0xD1,0x80,0xD1,0x83,0xD0,0xB6,0xD0,0xB5,0xD0,0xBD,0xD0,0xBE
)) "clean message utf8"
Assert-Contains $src (From-Utf8Bytes @(
    0xD0,0x9F,0xD1,0x80,0xD0,0xBE,0xD0,0xB2,0xD0,0xB5,0xD1,0x80,0xD0,0xBA,0xD0,0xB0,0x20,
    0xD0,0xB4,0xD1,0x83,0xD0,0xB1,0xD0,0xBB,0xD0,0xB5,0xD0,0xB9,0x20,
    0xD1,0x81,0xD0,0xBE,0xD1,0x82,0xD1,0x80,0xD1,0x83,0xD0,0xB4,0xD0,0xBD,0xD0,0xB8,0xD0,0xBA,0xD0,0xBE,0xD0,0xB2
)) "modal title utf8"
Assert-Contains $src (From-Utf8Bytes @(
    0x6D,0x61,0x74,0x63,0x68,0x54,0x79,0x70,0x65,0x3A,0x20,0x22,
    0xD0,0x9E,0xD0,0xB4,0xD0,0xB8,0xD0,0xBD,0xD0,0xB0,0xD0,0xBA,0xD0,0xBE,0xD0,0xB2,0xD1,0x8B,0xD0,0xB9,0x20,0x49,0x44,0x22
)) "id matchType utf8"
Assert-Contains $src (From-Utf8Bytes @(
    0x6D,0x61,0x74,0x63,0x68,0x54,0x79,0x70,0x65,0x3A,0x20,0x22,
    0xD0,0x9E,0xD0,0xB4,0xD0,0xB8,0xD0,0xBD,0xD0,0xB0,0xD0,0xBA,0xD0,0xBE,0xD0,0xB2,0xD0,0xBE,0xD0,0xB5,0x20,
    0xD0,0xA4,0xD0,0x98,0xD0,0x9E,0x22
)) "name matchType utf8"

$dupStart = $src.IndexOf("function normalizeEmployeeNameForDupCheck(")
$dupEnd = $src.IndexOf("`nfunction employeeDuplicateIdsReportHtml(", $dupStart)
if ($dupEnd -lt 0) { $dupEnd = $dupStart + 12000 }
$dupBlock = $src.Substring($dupStart, [Math]::Min($dupEnd - $dupStart, $src.Length - $dupStart))
Assert-NotContains $dupBlock (From-Utf8Bytes @(0xD0,0xA3,0xD0,0xB4,0xD0,0xB0,0xD0,0xBB,0xD0,0xB8,0xD1,0x82,0xD1,0x8C,0x20,0xD0,0xB4,0xD1,0x83,0xD0,0xB1,0xD0,0xBB,0xD1,0x8C)) "no delete-dup button"
Assert-NotContains $dupBlock (From-Utf8Bytes @(0xD0,0x9E,0xD0,0xB1,0xD1,0x8A,0xD0,0xB5,0xD0,0xB4,0xD0,0xB8,0xD0,0xBD,0xD0,0xB8,0xD1,0x82,0xD1,0x8C)) "no merge button"
Assert-NotContains $dupBlock (From-Utf8Bytes @(0xD0,0x98,0xD1,0x81,0xD0,0xBF,0xD1,0x80,0xD0,0xB0,0xD0,0xB2,0xD0,0xB8,0xD1,0x82,0xD1,0x8C,0x20,0x49,0x44)) "no fix-id button"
Assert-NotContains $dupBlock 'method: "PUT"' "no PUT in dup-check path"
Assert-NotContains $dupBlock 'method: "POST"' "no POST in dup-check path"
Assert-NotContains $dupBlock "migrateAppData(" "no migrate on check"
Assert-NotContains $dupBlock "distributeEmployeesPerDepartment(" "no distribute on check"
Assert-NotContains $dupBlock "removeEmployeeFromWorkspace(" "no remove on check"
Assert-NotContains $dupBlock "saveApp(" "no saveApp on check"
Assert-Contains $dupBlock 'method: "GET"' "GET state only in dup-check path"

$filtersStart = $src.IndexOf('id="staffEmployeeSearch"')
$filtersSlice = $src.Substring($filtersStart, [Math]::Min(900, $src.Length - $filtersStart))
Assert-Contains $filtersSlice "employeeDupCheckBtn" "button after search field"

Write-Host ""
Write-Host "=== Synthetic report logic ==="

function Norm-Name([string]$name) {
    return (($name -replace "\s+", " ").Trim().ToLowerInvariant())
}

function Build-Report($payload) {
    $codes = @("shop", "warehouse", "office", "drivers")
    $deptCounts = @{}
    $entries = New-Object System.Collections.Generic.List[object]
    foreach ($code in $codes) {
        $ws = @($payload.workspaces) | Where-Object { $_.code -eq $code } | Select-Object -First 1
        $list = @()
        if ($ws -and $ws.employees) { $list = @($ws.employees) }
        $deptCounts[$code] = $list.Count
        for ($i = 0; $i -lt $list.Count; $i++) {
            $e = $list[$i]
            if (-not $e) { continue }
            $id = ([string]$e.id).Trim()
            $name = ([string]$e.name).Trim()
            $entries.Add([pscustomobject]@{
                deptCode = $code
                id = $id
                name = $name
                nameKey = (Norm-Name $name)
                index = $i
            }) | Out-Null
        }
    }
    $byId = @{}
    $byName = @{}
    foreach ($row in $entries) {
        if ($row.id) {
            if (-not $byId.ContainsKey($row.id)) { $byId[$row.id] = New-Object System.Collections.Generic.List[object] }
            $byId[$row.id].Add($row) | Out-Null
        }
        if ($row.nameKey) {
            if (-not $byName.ContainsKey($row.nameKey)) { $byName[$row.nameKey] = New-Object System.Collections.Generic.List[object] }
            $byName[$row.nameKey].Add($row) | Out-Null
        }
    }
    $idGroups = @($byId.Keys | Where-Object { $byId[$_].Count -gt 1 })
    $nameGroups = @($byName.Keys | Where-Object { $byName[$_].Count -gt 1 })
    return [pscustomobject]@{
        deptCounts = $deptCounts
        idConflictCount = $idGroups.Count
        nameConflictCount = $nameGroups.Count
    }
}

# 1) Same ID inside warehouse
$p1 = @{
    workspaces = @(
        @{ code = "shop"; employees = @() },
        @{ code = "warehouse"; employees = @(
            @{ id = "emp_010"; name = "Kirillov Alexander" },
            @{ id = "emp_010"; name = "Kirillov Alexander" }
        ) },
        @{ code = "office"; employees = @() },
        @{ code = "drivers"; employees = @() }
    )
}
$r1 = Build-Report $p1
Assert-True ($r1.idConflictCount -eq 1) "same id inside warehouse detected"
Assert-True ($r1.deptCounts["warehouse"] -eq 2) "warehouse count 2"

# 2) Same ID across departments
$p2 = @{
    workspaces = @(
        @{ code = "shop"; employees = @(@{ id = "emp_099"; name = "Ivanov Ivan" }) },
        @{ code = "warehouse"; employees = @(@{ id = "emp_099"; name = "Ivanov Ivan" }) },
        @{ code = "office"; employees = @() },
        @{ code = "drivers"; employees = @() }
    )
}
$r2 = Build-Report $p2
Assert-True ($r2.idConflictCount -eq 1) "same id across depts detected"

# 3) Same FIO different IDs (spaces + case)
$p3 = @{
    workspaces = @(
        @{ code = "shop"; employees = @() },
        @{ code = "warehouse"; employees = @(
            @{ id = "emp_001"; name = "Andreev  Alexander" },
            @{ id = "emp_002"; name = "andreev alexander" }
        ) },
        @{ code = "office"; employees = @() },
        @{ code = "drivers"; employees = @() }
    )
}
$r3 = Build-Report $p3
Assert-True ($r3.nameConflictCount -eq 1) "same FIO different ids (spaces/case)"
Assert-True ($r3.idConflictCount -eq 0) "no id conflict for different ids"

# 4) Employee without duplicates
$p4 = @{
    workspaces = @(
        @{ code = "shop"; employees = @(@{ id = "V-000001"; name = "Unique Employee" }) },
        @{ code = "warehouse"; employees = @() },
        @{ code = "office"; employees = @() },
        @{ code = "drivers"; employees = @() }
    )
}
$r4 = Build-Report $p4
Assert-True (($r4.idConflictCount -eq 0) -and ($r4.nameConflictCount -eq 0)) "unique employee clean"

# 5) Empty lists
$p5 = @{
    workspaces = @(
        @{ code = "shop"; employees = @() },
        @{ code = "warehouse"; employees = @() },
        @{ code = "office"; employees = @() },
        @{ code = "drivers"; employees = @() }
    )
}
$r5 = Build-Report $p5
Assert-True (($r5.idConflictCount -eq 0) -and ($r5.nameConflictCount -eq 0)) "empty staff clean"
Assert-True ($r5.deptCounts["shop"] -eq 0) "empty shop count"

# 6) Error path marker exists (client shows message; no silent local fallback in server mode)
Assert-Contains $src "emp-dup-msg err" "server fail UI path exists"
Assert-Contains $src "if (typeof wtServerMode !== `"undefined`" && wtServerMode)" "server mode branch"

# 7) Analysis must not mutate arrays
$pMut = @{
    workspaces = @(
        @{ code = "warehouse"; employees = @(
            @{ id = "emp_010"; name = "A" },
            @{ id = "emp_010"; name = "B" }
        ) }
    )
}
$before = ($pMut | ConvertTo-Json -Depth 8 -Compress)
[void](Build-Report $pMut)
$after = ($pMut | ConvertTo-Json -Depth 8 -Compress)
Assert-True ($before -eq $after) "payload arrays unchanged by analysis"

# 8) Admin-only visibility wiring
Assert-Contains $src "wrap.style.display = (typeof isAppAdmin === `"function`" && isAppAdmin())" "admin-only display"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_employee_dup_check.ps1 checks passed."
exit 0
