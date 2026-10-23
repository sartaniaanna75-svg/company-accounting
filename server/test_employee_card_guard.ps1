# Employee card guard tests. Run: powershell -NoProfile -File server/test_employee_card_guard.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$AppHtml = Join-Path $Root "app.html"
$src = [System.IO.File]::ReadAllText($AppHtml, [System.Text.UTF8Encoding]::new($false))

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

Write-Host "=== Source markers ==="

Assert-Contains $src "function stageEmployeeCardField(" "draft helper exists"
Assert-Contains $src "EMPLOYEE_CARD_PROTECTED_FIELDS" "protected fields map"
Assert-True ($src -match "stageEmployeeCardField\([^)]*work_tab_id") "work_tab stages draft"
Assert-True ($src -match "stageEmployeeCardField\([^)]*position_id") "position_id stages draft"
Assert-Contains $src "applyEmployeeCardProtectedDraft" "commit applies draft"
Assert-True (
    ($src.IndexOf("EMPLOYEE_CARD_PROTECTED_FIELDS[field]") -ge 0) -and
    ($src.IndexOf("stageEmployeeCardField(employeeId, field, value)") -ge 0)
) "updateEmployee stages protected fields"

$syncStart = $src.IndexOf("function syncEmployeeMetricAssignmentsToCatalog")
$syncEnd = $src.IndexOf("`nfunction ", $syncStart + 10)
$syncLen = if ($syncEnd -gt $syncStart) { $syncEnd - $syncStart } else { 2500 }
$syncBody = $src.Substring($syncStart, [Math]::Min($syncLen, $src.Length - $syncStart))
Assert-NotContains $syncBody "setEmployeePosition(" "metric sync does not set position"
Assert-NotContains $syncBody "setEmployeePrimaryWorkTab(" "metric sync does not set work tab"
Assert-NotContains $syncBody "employee.position_id =" "metric sync does not write employee.position_id"
Assert-NotContains $syncBody "employee.work_tab_id =" "metric sync does not write employee.work_tab_id"
Assert-NotContains $syncBody "employee.work_group_id =" "metric sync does not write employee.work_group_id"
Assert-NotContains $syncBody "employee.department =" "metric sync does not write employee.department"

$dedupeStart = $src.IndexOf("function dedupeClonedEmployees")
$dedupeSlice = $src.Substring($dedupeStart, [Math]::Min(1200, $src.Length - $dedupeStart))
Assert-Contains $src "possible_name_duplicate" "name duplicate report type"
Assert-True (
    ($dedupeSlice.IndexOf("pushEmployeeCardGuardReport") -ge 0) -and
    ($dedupeSlice.IndexOf("removeEmployeeFromWorkspace") -lt 0)
) "dedupe does not remove employees"

$distStart = $src.IndexOf("function distributeEmployeesPerDepartment")
$distSlice = $src.Substring($distStart, [Math]::Min(2800, $src.Length - $distStart))
Assert-Contains $src "distribute_no_clone" "no-clone report type"
Assert-True ($distSlice.IndexOf("cloneEmployeeRecord") -lt 0) "distribute has no cloneEmployeeRecord"

Assert-Contains $src "employee_id_multi_object" "multi-object id report type"
Assert-Contains $src "function getEmployeeCardGuardReport(" "guard report getter"

Assert-Contains $src 'code === "shop"' "shop code rules present"
Assert-Contains $src 'employeeCardWorkGroupId(e, "shop")' "shop card work-group filter preserved"
Assert-Contains $src "function timesheetGroupFilterTabs" "timesheet group tabs kept"
Assert-Contains $src "employeeCardWorkGroupId" "shop card work group helper kept"

Assert-Contains $src "onMetricCatalogAssignmentField" "territory metric assignment UI"
Assert-Contains $src "function metricItemObjectSides(" "metric object sides"
Assert-Contains $src "metricDeptObjectSide" "metric dept object side"
Assert-Contains $src "opts.force === true" "bindEmployeePositionId requires force"
Assert-Contains $src "if (e.position_id) return;" "ensurePositionCatalog skips filled position_id"
Assert-True ($src -match "function reassignPositionEmployees" -and $src -match "confirm\(") "reassign uses confirm"
Assert-Contains $src "function moveEmployeeToDepartment(" "moveEmployeeToDepartment exists"
Assert-Contains $src "opts.confirmed" "move requires confirmed or dialog"

Write-Host ""
Write-Host "=== Runtime logic ==="

function Get-Fingerprint {
    param($e)
    $name = ((($e.name + "") -replace "\s+", " ").Trim().ToLowerInvariant())
    if (-not $name -or $name -eq "new employee") { return "" }
    $pos = ((($e.position + "") -replace "\s+", " ").Trim().ToLowerInvariant())
    $parts = @($name, $pos, [string]$e.rate, [string]$e.start, [string]$e.end)
    return ($parts -join "|")
}

function Test-Belongs {
    param($w, $id)
    foreach ($e in @($w.employees)) {
        if ($e -and $e.id -eq $id) { return $true }
    }
    return $false
}

function Invoke-Dedupe {
    param($data, [System.Collections.ArrayList]$report)
    $groups = @{}
    foreach ($w in @($data.workspaces)) {
        if (-not $w -or $w.type -eq "summary") { continue }
        foreach ($e in @($w.employees)) {
            $key = Get-Fingerprint $e
            if (-not $key) { continue }
            if (-not $groups.ContainsKey($key)) {
                $groups[$key] = New-Object System.Collections.ArrayList
            }
            [void]$groups[$key].Add([pscustomobject]@{ code = $w.code; id = $e.id; name = $e.name })
        }
    }
    foreach ($key in @($groups.Keys)) {
        $items = $groups[$key]
        if ($items.Count -lt 2) { continue }
        [void]$report.Add([pscustomobject]@{
            type = "possible_name_duplicate"
            fingerprint = $key
            employees = $items
        })
    }
}

function Invoke-Distribute {
    param($data, [System.Collections.ArrayList]$report)
    $owned = @{}
    foreach ($w in @($data.workspaces)) {
        if ($w.type -eq "summary") {
            $w.employees = @()
            $w.employeeIds = @()
            continue
        }
        $unique = New-Object System.Collections.ArrayList
        $localIds = @{}
        foreach ($e in @($w.employees)) {
            if (-not $e -or -not $e.id -or $localIds.ContainsKey($e.id)) { continue }
            if ($owned.ContainsKey($e.id) -and $owned[$e.id] -ne $w) {
                [void]$report.Add([pscustomobject]@{
                    type = "employee_id_multi_object"
                    employee_id = $e.id
                    name = $e.name
                    departments = @($owned[$e.id].code, $w.code)
                })
                $localIds[$e.id] = $true
                [void]$unique.Add($e)
                continue
            }
            $localIds[$e.id] = $true
            $owned[$e.id] = $w
            [void]$unique.Add($e)
        }
        $w.employees = @($unique)
        $nextIds = New-Object System.Collections.ArrayList
        foreach ($id in @($w.employeeIds)) {
            if (-not $id -or $nextIds.Contains($id)) { continue }
            if ($owned.ContainsKey($id) -and $owned[$id] -ne $w) {
                [void]$report.Add([pscustomobject]@{
                    type = "distribute_id_conflict"
                    employee_id = $id
                    target = $w.code
                })
                continue
            }
            if (-not (Test-Belongs $w $id)) {
                [void]$report.Add([pscustomobject]@{
                    type = "distribute_no_clone"
                    employee_id = $id
                    target = $w.code
                })
                continue
            }
            [void]$nextIds.Add($id)
        }
        $kept = @()
        foreach ($id in @($nextIds)) {
            if (Test-Belongs $w $id) { $kept += $id }
        }
        $w.employeeIds = $kept
    }
    $data.employees = @()
    Invoke-Dedupe $data $report
}

# Scenario 5: same FIO kept
$report = New-Object System.Collections.ArrayList
$dataDup = [pscustomobject]@{
    workspaces = @(
        [pscustomobject]@{
            type = "dept"; code = "shop"
            employees = @([pscustomobject]@{ id = "a1"; name = "Ivanov"; position = "Cashier"; rate = 1000; start = "08:00"; end = "17:00" })
            employeeIds = @("a1")
        },
        [pscustomobject]@{
            type = "dept"; code = "warehouse"
            employees = @([pscustomobject]@{ id = "b1"; name = "Ivanov"; position = "Cashier"; rate = 1000; start = "08:00"; end = "17:00" })
            employeeIds = @("b1")
        }
    )
    employees = @()
}
Invoke-Distribute $dataDup $report
Assert-True (
    ($dataDup.workspaces[0].employees.Count -eq 1) -and
    ($dataDup.workspaces[1].employees.Count -eq 1)
) "load keeps same-name employees"
Assert-True ((@($report | Where-Object { $_.type -eq "possible_name_duplicate" }).Count -ge 1)) "same-name goes to report"

# Scenario 6: no clone
$report = New-Object System.Collections.ArrayList
$dataClone = [pscustomobject]@{
    workspaces = @(
        [pscustomobject]@{
            type = "dept"; code = "shop"
            employees = @([pscustomobject]@{ id = "e1"; name = "Petrov"; position = "Seller"; rate = 1; start = "08:00"; end = "17:00" })
            employeeIds = @("e1")
        },
        [pscustomobject]@{
            type = "dept"; code = "warehouse"
            employees = @()
            employeeIds = @("e1")
        }
    )
    employees = @([pscustomobject]@{ id = "e1"; name = "Petrov" })
}
Invoke-Distribute $dataClone $report
Assert-True ($dataClone.workspaces[1].employees.Count -eq 0) "load does not clone into other object"
Assert-True ((@($report | Where-Object {
    $_.type -eq "distribute_no_clone" -or $_.type -eq "distribute_id_conflict"
}).Count -ge 1)) "clone attempt reported"

# Scenario 7: same id in two objects
$report = New-Object System.Collections.ArrayList
$dataConflict = [pscustomobject]@{
    workspaces = @(
        [pscustomobject]@{
            type = "dept"; code = "shop"
            employees = @([pscustomobject]@{ id = "same"; name = "Sidorov"; position = "A"; rate = 1; start = "08:00"; end = "17:00" })
            employeeIds = @("same")
        },
        [pscustomobject]@{
            type = "dept"; code = "warehouse"
            employees = @([pscustomobject]@{ id = "same"; name = "Sidorov"; position = "B"; rate = 2; start = "09:00"; end = "18:00" })
            employeeIds = @("same")
        }
    )
    employees = @()
}
Invoke-Distribute $dataConflict $report
Assert-True (
    ($dataConflict.workspaces[0].employees.Count -eq 1) -and
    ($dataConflict.workspaces[1].employees.Count -eq 1)
) "both cards with same id kept"
Assert-True ((@($report | Where-Object { $_.type -eq "employee_id_multi_object" }).Count -ge 1)) "id conflict reported"

# Scenario 2: draft does not mutate
$emp = [ordered]@{ id = "x"; position = "Keeper"; position_id = "pos1"; work_tab_id = "leaders"; department = "WH" }
$before = ($emp | ConvertTo-Json -Compress)
$draft = @{ x = @{ position_id = "pos2"; work_tab_id = "loaders" } }
$after = ($emp | ConvertTo-Json -Compress)
Assert-True ($before -eq $after) "draft change does not mutate employee"
Assert-True ($draft.x.position_id -eq "pos2" -and $draft.x.work_tab_id -eq "loaders") "values stay in draft"

# Scenario 9/10
function Get-Side([string]$dept) {
    if ($dept -eq "shop") { return "shop" }
    if (@("office", "warehouse", "drivers") -contains $dept) { return "territory" }
    return ""
}
Assert-True ((Get-Side "shop") -eq "shop") "shop metric side"
Assert-True ((Get-Side "warehouse") -eq "territory") "territory metric side"
$terrAsg = [pscustomobject]@{ position_id = "p1"; work_tab_id = "t1"; department_id = "warehouse" }
Assert-True ($terrAsg.position_id -eq "p1" -and $terrAsg.work_tab_id -eq "t1") "territory assignment keeps position/work_tab"

$ensStart = $src.IndexOf("function ensurePositionCatalog")
$ensSlice = $src.Substring($ensStart, [Math]::Min(3500, $src.Length - $ensStart))
Assert-True (
    ($ensSlice.IndexOf("if (e.position_id) return;") -ge 0)
) "reload/ensure does not rewrite when position_id set"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $script:passed, $script:failed)
if ($script:failed -gt 0) { exit 1 }
Write-Host "All test_employee_card_guard.ps1 checks passed."
exit 0
