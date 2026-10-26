# Isolated pure-logic harness for ops foundation (no Python / no PostgreSQL).
# Mirrors critical wt_api helpers used by POST /api/state/op and protect_wt_records_on_put.
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File server/test_wt_ops_logic.ps1
$ErrorActionPreference = "Stop"
$passed = 0
$failed = 0
$script:failures = @()

function Ok($cond, $msg) {
    if ($cond) {
        $script:passed++
        Write-Host "  OK  $msg"
    } else {
        $script:failed++
        $script:failures += $msg
        Write-Host "  FAIL  $msg" -ForegroundColor Red
    }
}

function Clone-Obj($o) {
    if ($null -eq $o) { return $null }
    return (ConvertFrom-Json (ConvertTo-Json $o -Depth 40 -Compress))
}

function Set-Note($obj, [string]$name, $value) {
    # Числовые имена свойств (ячейки табеля "1","2") через Add-Member в PS5 ломаются.
    $ht = [ordered]@{}
    if ($null -ne $obj) {
        foreach ($p in $obj.PSObject.Properties) { $ht[$p.Name] = $p.Value }
    }
    if ($null -eq $value) { $ht.Remove($name) }
    else { $ht[$name] = $value }
    return [pscustomobject]$ht
}

function Get-PathValue($root, [string]$path) {
    if ([string]::IsNullOrEmpty($path)) { return $null }
    $parts = $path.Split(".")
    $cur = $root
    $i = 0
    while ($i -lt $parts.Length) {
        $part = $parts[$i]
        if ($part -eq "workspaces" -and $cur -is [pscustomobject] -and ($cur.PSObject.Properties.Name -contains "workspaces") -and ($cur.workspaces -is [System.Collections.IEnumerable]) -and -not ($cur.workspaces -is [string])) {
            if ($i + 1 -ge $parts.Length) { return $cur.workspaces }
            $code = $parts[$i + 1]
            $found = $null
            foreach ($ws in @($cur.workspaces)) {
                if ($ws.code -eq $code) { $found = $ws; break }
            }
            if ($null -eq $found) { return $null }
            $cur = $found
            $i += 2
            continue
        }
        if ($cur -is [pscustomobject] -and ($cur.PSObject.Properties.Name -contains $part)) {
            $cur = $cur.$part
            $i++
            continue
        }
        if ($cur -is [hashtable] -and $cur.ContainsKey($part)) {
            $cur = $cur[$part]
            $i++
            continue
        }
        return $null
    }
    return $cur
}

function Set-PathValue($root, [string]$path, $value) {
    $out = Clone-Obj $root
    $parts = $path.Split(".")
    if ($parts[0] -eq "workspaces" -and ($out.workspaces -is [System.Collections.IEnumerable]) -and -not ($out.workspaces -is [string])) {
        if ($parts.Length -lt 2) { return $out }
        $code = $parts[1]
        $list = @($out.workspaces | ForEach-Object { Clone-Obj $_ })
        $idx = -1
        for ($j = 0; $j -lt $list.Count; $j++) {
            if ($list[$j].code -eq $code) { $idx = $j; break }
        }
        if ($idx -lt 0) { return $out }
        if ($parts.Length -eq 2) {
            if ($null -eq $value) {
                $list = @($list | Where-Object { $_.code -ne $code })
            } else {
                $list[$idx] = Clone-Obj $value
            }
            $out.workspaces = $list
            return $out
        }
        # Пересобираем вложенность от листа к корню workspace.
        $ws = Clone-Obj $list[$idx]
        $chain = @($ws)
        $cur = $ws
        for ($k = 2; $k -lt $parts.Length - 1; $k++) {
            $p = $parts[$k]
            $next = $null
            if ($cur.PSObject.Properties.Name -contains $p) { $next = $cur.$p }
            if ($null -eq $next) { $next = [pscustomobject]@{} }
            $chain += $next
            $cur = $next
        }
        $leaf = [string]$parts[-1]
        $chain[$chain.Length - 1] = Set-Note $chain[-1] $leaf (Clone-Obj $value)
        for ($k = $parts.Length - 2; $k -ge 2; $k--) {
            $parentIdx = $k - 2
            $childIdx = $parentIdx + 1
            $chain[$parentIdx] = Set-Note $chain[$parentIdx] ([string]$parts[$k]) $chain[$childIdx]
        }
        $list[$idx] = $chain[0]
        $out.workspaces = $list
        return $out
    }
    $chain = @($out)
    $cur = $out
    for ($k = 0; $k -lt $parts.Length - 1; $k++) {
        $p = $parts[$k]
        $next = $null
        if ($cur.PSObject.Properties.Name -contains $p) { $next = $cur.$p }
        if ($null -eq $next) { $next = [pscustomobject]@{} }
        $chain += $next
        $cur = $next
    }
    $leaf = [string]$parts[-1]
    $chain[$chain.Length - 1] = Set-Note $chain[-1] $leaf (Clone-Obj $value)
    for ($k = $parts.Length - 2; $k -ge 0; $k--) {
        $chain[$k] = Set-Note $chain[$k] ([string]$parts[$k]) $chain[$k + 1]
    }
    return $chain[0]
}

function Records-Map($payload) {
    if ($null -eq $payload) { return @{} }
    $raw = $payload._wt_records
    if ($null -eq $raw) { return @{} }
    $map = @{}
    foreach ($p in $raw.PSObject.Properties) {
        $map[$p.Name] = $p.Value
    }
    return $map
}

function Record-Version($payload, [string]$path) {
    $map = Records-Map $payload
    if (-not $map.ContainsKey($path)) { return 0 }
    $meta = $map[$path]
    try { return [int]$meta.v } catch { return 0 }
}

function Bump-RecordMeta($payload, [string]$path, [string]$userId) {
    $out = Clone-Obj $payload
    $prev = Record-Version $out $path
    $map = Records-Map $out
    $map[$path] = [pscustomobject]@{ v = ($prev + 1); updated_by = $userId }
    $bag = [pscustomobject]@{}
    foreach ($k in $map.Keys) {
        $bag | Add-Member -NotePropertyName $k -NotePropertyValue $map[$k] -Force
    }
    $out | Add-Member -NotePropertyName "_wt_records" -NotePropertyValue $bag -Force
    return $out
}

function Protect-WtRecords($current, $stored, $incoming) {
    $curMap = Records-Map $current
    if ($curMap.Count -eq 0) {
        $out = Clone-Obj $stored
        if (($current.PSObject.Properties.Name -contains "_wt_records") -and -not ($incoming.PSObject.Properties.Name -contains "_wt_records")) {
            $out | Add-Member -NotePropertyName "_wt_records" -NotePropertyValue (Clone-Obj $current._wt_records) -Force
        }
        return $out
    }
    $inMap = Records-Map $incoming
    $out = Clone-Obj $stored
    foreach ($path in $curMap.Keys) {
        $serverV = 0
        try { $serverV = [int]$curMap[$path].v } catch { $serverV = 0 }
        $clientV = 0
        if ($inMap.ContainsKey($path)) {
            try { $clientV = [int]$inMap[$path].v } catch { $clientV = 0 }
        }
        if ($clientV -gt $serverV) { $clientV = 0 }
        if ($serverV -gt $clientV) {
            $out = Set-PathValue $out $path (Get-PathValue $current $path)
        }
    }
    $bag = [pscustomobject]@{}
    foreach ($k in $curMap.Keys) {
        $bag | Add-Member -NotePropertyName $k -NotePropertyValue (Clone-Obj $curMap[$k]) -Force
    }
    $out | Add-Member -NotePropertyName "_wt_records" -NotePropertyValue $bag -Force
    return $out
}

function Apply-StateOp($payload, $op, $isAdmin, $codes, $canInput, $canSetup) {
    $path = [string]$op.path
    $parts = $path.Split(".")
    $top = $parts[0]
    $allowed = $false
    if ($isAdmin) { $allowed = $true }
    elseif ($top -eq "shopMetrics") {
        if ($codes -contains "shop" -and $parts.Length -ge 2) {
            $branch = $parts[1]
            if ($branch -in @("store_id", "storeMeta", "substitutionPayDefaults")) { $allowed = $false }
            elseif ($branch -in @("workAccess", "extraAccess")) { $allowed = $canSetup }
            elseif ($branch -in @("facts", "cashiers", "pickers", "managers", "channelValues", "store", "channels", "entries")) { $allowed = ($canInput -or $canSetup) }
            else { $allowed = $false }
        }
    }
    elseif ($top -eq "deptMetrics") {
        if ($parts.Length -ge 3 -and ($codes -contains $parts[1])) {
            $branch = $parts[2]
            if ($branch -eq "facts") { $allowed = ($canInput -or $canSetup) }
            elseif ($branch -in @("workAccess", "extraAccess")) { $allowed = $canSetup }
            else { $allowed = $false }
        }
    }
    elseif ($top -eq "workspaces") {
        if ($parts.Length -ge 3 -and ($codes -contains $parts[1]) -and $parts[2] -eq "timesheet") { $allowed = $true }
    }
    if (-not $allowed) {
        return @{ ok = $false; error = "forbidden"; payload = $payload }
    }
    if ($path.StartsWith("workspaces.")) {
        $wcode = $parts[1]
        $hit = $false
        foreach ($ws in @($payload.workspaces)) { if ($ws.code -eq $wcode) { $hit = $true; break } }
        if (-not $hit) { return @{ ok = $false; error = "invalid path"; payload = $payload } }
    }
    $expected = [int]$op.expected_v
    $serverV = Record-Version $payload $path
    if ($expected -ne $serverV) {
        return @{
            ok = $false
            error = "conflict"
            server_v = $serverV
            server_value = (Get-PathValue $payload $path)
            payload = $payload
        }
    }
    $next = Set-PathValue $payload $path $op.value
    $next = Bump-RecordMeta $next $path "u"
    return @{ ok = $true; payload = $next; record_v = (Record-Version $next $path); value = (Get-PathValue $next $path) }
}

# --- Scenario store ---
$state = [pscustomobject]@{
    version = 3
    payload = [pscustomobject]@{
        workspaces = @(
            [pscustomobject]@{ code = "shop"; timesheet = [pscustomobject]@{ "2026-10" = [pscustomobject]@{ e_shop = [pscustomobject]@{ "1" = "Я" } } } },
            [pscustomobject]@{ code = "warehouse"; timesheet = [pscustomobject]@{ "2026-10" = [pscustomobject]@{ e_wh = [pscustomobject]@{ "1" = "Я" } } } }
        )
        shopMetrics = [pscustomobject]@{ facts = [pscustomobject]@{ ok = 1 } }
        deptMetrics = [pscustomobject]@{ warehouse = [pscustomobject]@{ facts = [pscustomobject]@{ secret = 1 } } }
    }
}
$idem = @{}

function Do-Op($path, $expected, $value, $key, $isAdmin, $codes, $canInput, $canSetup) {
    if ($key -and $idem.ContainsKey($key)) {
        return $idem[$key]
    }
    $r = Apply-StateOp $state.payload @{ path = $path; expected_v = $expected; value = $value } $isAdmin $codes $canInput $canSetup
    if ($r.ok) {
        $state.payload = $r.payload
        $state.version++
        $body = @{ ok = $true; value = $r.value; record_v = $r.record_v; version = $state.version }
        if ($key) { $idem[$key] = $body }
        return $body
    }
    return $r
}

Write-Host "=== 1-2. Two independent ops + op vs stale PUT ==="
$r1 = Do-Op "shopMetrics.facts.picker_a" 0 10 "k1" $true @("shop") $true $true
$r2 = Do-Op "shopMetrics.facts.cashier_b" 0 20 "k2" $true @("shop") $true $true
Ok ($r1.ok -and $r2.ok) "two independent ops succeed"
Ok (((Get-PathValue $state.payload "shopMetrics.facts.picker_a") -eq 10) -and ((Get-PathValue $state.payload "shopMetrics.facts.cashier_b") -eq 20)) "both leaves kept"
Ok ((Get-PathValue $state.payload "shopMetrics.facts.ok") -eq 1) "sibling fact ok preserved"

# op then stale PUT without record map
$before = Get-PathValue $state.payload "shopMetrics.facts.picker_a"
$storedPut = Clone-Obj $state.payload
$storedPut.shopMetrics.facts | Add-Member -NotePropertyName "picker_a" -NotePropertyValue "STALE" -Force
$protected = Protect-WtRecords $state.payload $storedPut ([pscustomobject]@{ shopMetrics = $storedPut.shopMetrics; _wt_records = [pscustomobject]@{} })
Ok ((Get-PathValue $protected "shopMetrics.facts.picker_a") -eq $before) "stale PUT restored op leaf"
Ok ((Record-Version $protected "shopMetrics.facts.picker_a") -ge 1) "server record meta kept"

Write-Host "=== 3. Stale PUT after successful op ==="
$rG = Do-Op "shopMetrics.facts.guarded" 0 "from_op" $null $true @("shop") $true $true
$staleStored = Clone-Obj $state.payload
$staleStored.shopMetrics = [pscustomobject]@{ facts = [pscustomobject]@{ ok = 1 } }
$prot2 = Protect-WtRecords $state.payload $staleStored ([pscustomobject]@{ _wt_records = [pscustomobject]@{} })
Ok ((Get-PathValue $prot2 "shopMetrics.facts.guarded") -eq "from_op") "missing leaf restored from server after op"

Write-Host "=== 4-6. Same record conflict, confirm, third writer ==="
$ra = Do-Op "shopMetrics.facts.cell" 0 "A" $null $true @("shop") $true $true
$rb = Do-Op "shopMetrics.facts.cell" 0 "B" $null $true @("shop") $true $true
Ok ($ra.ok -and (-not $rb.ok) -and $rb.error -eq "conflict") "second writer conflict on same path"
Ok ($rb.server_value -eq "A") "conflict returns server value A"
$rc = Do-Op "shopMetrics.facts.cell" 1 "B" $null $true @("shop") $true $true
Ok ($rc.ok -and $rc.value -eq "B") "confirm replace with expected_v=1"
# third writer before confirm: reset race path
$rA0 = Do-Op "shopMetrics.facts.race" 0 "A" $null $true @("shop") $true $true
$rC = Do-Op "shopMetrics.facts.race" 1 "C" $null $true @("shop") $true $true
$rB = Do-Op "shopMetrics.facts.race" 1 "B" $null $true @("shop") $true $true
Ok ($rC.ok -and (-not $rB.ok) -and $rB.server_value -eq "C") "third writer invalidates stale confirm"

Write-Host "=== 7-8. Idempotency + reconnect replay ==="
$i1 = Do-Op "shopMetrics.facts.once" 0 7 "same-key" $true @("shop") $true $true
$ver1 = $state.version
$i2 = Do-Op "shopMetrics.facts.once" 0 7 "same-key" $true @("shop") $true $true
Ok ($i1.ok -and $i2.ok -and $i2.value -eq 7 -and $state.version -eq $ver1) "idempotent replay does not double-write"
Ok ((Get-PathValue $state.payload "shopMetrics.facts.once") -eq 7) "value stable after replay"

Write-Host "=== 9. Different groups (shop vs warehouse) ==="
$shopOp = Do-Op "shopMetrics.facts.group_shop" 0 11 $null $false @("shop") $true $false
$whOp = Do-Op "deptMetrics.warehouse.facts.group_wh" 0 22 $null $false @("warehouse") $true $false
Ok ($shopOp.ok -and $whOp.ok) "shop and warehouse ops both ok"
Ok (((Get-PathValue $state.payload "shopMetrics.facts.group_shop") -eq 11) -and ((Get-PathValue $state.payload "deptMetrics.warehouse.facts.group_wh") -eq 22)) "cross-group values kept"
$deny = Do-Op "deptMetrics.warehouse.facts.secret2" 0 99 $null $false @("shop") $true $false
Ok ((-not $deny.ok) -and $deny.error -eq "forbidden") "shop staff cannot write warehouse path"

Write-Host "=== 10. Admin wide PUT + ACL branch + forged v + workspaces list ==="
$wideStored = Clone-Obj $state.payload
# admin "wide" fragment drops several op leaves in the merge result
$wideStored.shopMetrics = [pscustomobject]@{ facts = [pscustomobject]@{ ok = 1; admin_extra = 5 } }
$wideIn = [pscustomobject]@{ shopMetrics = $wideStored.shopMetrics; _wt_records = [pscustomobject]@{} }
$wideProt = Protect-WtRecords $state.payload $wideStored $wideIn
Ok ((Get-PathValue $wideProt "shopMetrics.facts.picker_a") -eq 10) "admin wide PUT keeps op picker_a"
Ok ((Get-PathValue $wideProt "shopMetrics.facts.cashier_b") -eq 20) "admin wide PUT keeps op cashier_b"
Ok ((Get-PathValue $wideProt "shopMetrics.facts.admin_extra") -eq 5) "admin legitimate new key allowed"

$setupDeny = Do-Op "shopMetrics.workAccess.2026-10.e_shop" 0 @("m1") $null $false @("shop") $true $false
Ok ((-not $setupDeny.ok) -and $setupDeny.error -eq "forbidden") "metricsInput cannot write workAccess via path"

$forgedStored = Clone-Obj $state.payload
$forgedStored.shopMetrics.facts | Add-Member -NotePropertyName "guarded" -NotePropertyValue "forged" -Force
$forgedIn = [pscustomobject]@{
    shopMetrics = $forgedStored.shopMetrics
    _wt_records = [pscustomobject]@{ "shopMetrics.facts.guarded" = [pscustomobject]@{ v = 99 } }
}
$forgedProt = Protect-WtRecords $state.payload $forgedStored $forgedIn
Ok ((Get-PathValue $forgedProt "shopMetrics.facts.guarded") -eq "from_op") "forged client_v cannot wipe op"
Ok ((Record-Version $forgedProt "shopMetrics.facts.guarded") -eq 1) "forged client_v cannot raise meta"

$wsOp = Do-Op "workspaces.shop.timesheet.2026-10.e_shop.2" 0 "В" $null $true @("shop") $true $true
Ok ($wsOp.ok) "workspaces op by code succeeds"
Ok (($state.payload.workspaces -is [System.Collections.IEnumerable]) -and (@($state.payload.workspaces).Count -eq 2)) "workspaces remains a list of 2"
$shopWs = @($state.payload.workspaces | Where-Object { $_.code -eq "shop" })[0]
Ok ($shopWs.timesheet."2026-10".e_shop."2" -eq "В") "shop timesheet cell written"
$whWs = @($state.payload.workspaces | Where-Object { $_.code -eq "warehouse" })[0]
Ok ($whWs.timesheet."2026-10".e_wh."1" -eq "Я") "warehouse sibling untouched"

Write-Host "=== Legitimate PUT when client_v matches (not blocked) ==="
$matchCurrent = Clone-Obj $state.payload
$pathM = "shopMetrics.facts.legit"
$matchCurrent = Set-PathValue $matchCurrent $pathM "old"
$matchCurrent = Bump-RecordMeta $matchCurrent $pathM "u"
$matchStored = Clone-Obj $matchCurrent
$matchStored = Set-PathValue $matchStored $pathM "new"
$matchIn = [pscustomobject]@{
    _wt_records = [pscustomobject]@{ $pathM = [pscustomobject]@{ v = 1 } }
}
$matchProt = Protect-WtRecords $matchCurrent $matchStored $matchIn
Ok ((Get-PathValue $matchProt $pathM) -eq "new") "matching client_v allows legitimate PUT change"
# После protect сервер должен bump record_v при реальном изменении значения (sync).
function Sync-AfterPut($current, $stored) {
    $out = Clone-Obj $stored
    # упрощённо: все tracked leaves из diff facts
    $beforeFacts = @{}
    $afterFacts = @{}
    if ($current.shopMetrics -and $current.shopMetrics.facts) {
        foreach ($p in $current.shopMetrics.facts.PSObject.Properties) { $beforeFacts[$p.Name] = $p.Value }
    }
    if ($out.shopMetrics -and $out.shopMetrics.facts) {
        foreach ($p in $out.shopMetrics.facts.PSObject.Properties) { $afterFacts[$p.Name] = $p.Value }
    }
    $names = @($beforeFacts.Keys + $afterFacts.Keys) | Select-Object -Unique
    foreach ($n in $names) {
        $bv = $beforeFacts[$n]; $av = $afterFacts[$n]
        if (("" + $bv) -ne ("" + $av)) {
            $path = "shopMetrics.facts.$n"
            $out = Bump-RecordMeta $out $path "u"
        }
    }
    return $out
}
$matchSynced = Sync-AfterPut $matchCurrent $matchProt
Ok ((Record-Version $matchSynced $pathM) -eq 2) "PUT change bumps record_v from 1 to 2"

Write-Host "=== PUT then stale op conflicts ==="
$putState = [pscustomobject]@{
    payload = [pscustomobject]@{ shopMetrics = [pscustomobject]@{ facts = [pscustomobject]@{ ok = 1 } } }
}
$putStored = Clone-Obj $putState.payload
$putStored = Set-PathValue $putStored "shopMetrics.facts.from_put" "PUT_VAL"
$putSynced = Sync-AfterPut $putState.payload $putStored
$putState.payload = $putSynced
Ok ((Record-Version $putState.payload "shopMetrics.facts.from_put") -ge 1) "PUT registers record_v for new leaf"
$staleOp = Apply-StateOp $putState.payload @{ path = "shopMetrics.facts.from_put"; expected_v = 0; value = "OP" } $true @("shop") $true $true
Ok ((-not $staleOp.ok) -and $staleOp.error -eq "conflict") "stale op after PUT conflicts"
Ok ($staleOp.server_value -eq "PUT_VAL") "conflict keeps PUT value"
$freshV = Record-Version $putState.payload "shopMetrics.facts.from_put"
$freshOp = Apply-StateOp $putState.payload @{ path = "shopMetrics.facts.from_put"; expected_v = $freshV; value = "OP" } $true @("shop") $true $true
Ok ($freshOp.ok -and $freshOp.value -eq "OP") "op with fresh record_v after PUT succeeds"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) {
    Write-Host "Failures:" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
    exit 1
}
Write-Host "All test_wt_ops_logic.ps1 checks passed."
Write-Host "NOTE: This harness mirrors logic; WtOpsFoundationTests still need a real Python interpreter."
exit 0
