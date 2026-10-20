# =============================================================================
# LOCAL DEMO ONLY — show_day_total checkbox ("Pokazyvat itogo za den")
# DELETE before publish:
#   _serve_local_demo_day_total.ps1
#   _local_demo_day_total_data.json
# No PostgreSQL / Timeweb. In-memory only.
# Port 8766 (plain static _serve.ps1 stays on 8765).
#
# Start:
#   powershell -NoProfile -ExecutionPolicy Bypass -File ".\_serve_local_demo_day_total.ps1"
# Open:
#   http://127.0.0.1:8766/app.html#territoryMetrics
# Login: user "Demo admin (local test)" / PIN 5555
# =============================================================================

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8766
$prefix = "http://127.0.0.1:$port/"
$month = (Get-Date).ToString("yyyy-MM")
$dataFile = Join-Path $root "_local_demo_day_total_data.json"
$metricId = "metric_demo_brought_money"
$empIds = @("demo_tp_1", "demo_tp_2", "demo_tp_3", "demo_tp_4", "demo_tp_5")

if (-not (Test-Path -LiteralPath $dataFile)) {
    Write-Output "Missing $dataFile"
    exit 1
}

$utf8 = New-Object System.Text.UTF8Encoding $false
$raw = [System.IO.File]::ReadAllText($dataFile, $utf8)
$script:Payload = $raw | ConvertFrom-Json

# Inject current-month workAccess for the five demo sales reps
if (-not $script:Payload.deptMetrics.drivers.workAccess) {
    $script:Payload.deptMetrics.drivers | Add-Member -NotePropertyName workAccess -NotePropertyValue ([pscustomobject]@{}) -Force
}
$waMap = [ordered]@{}
foreach ($id in $empIds) { $waMap[$id] = @($metricId) }
$script:Payload.deptMetrics.drivers.workAccess | Add-Member -NotePropertyName $month -NotePropertyValue ([pscustomobject]$waMap) -Force

$script:Version = 1
$script:Sessions = @{}

function ConvertTo-JsonDeep($obj) {
    return ($obj | ConvertTo-Json -Depth 60 -Compress)
}

function Get-PublicUser($u) {
    return @{
        user_id = $u.user_id
        display_name = $u.display_name
        role = $u.role
        legacy_role = $u.legacy_role
        is_active = $u.is_active
        employee_id = $u.employee_id
        departments = @($u.departments)
        login_codes = @($u.login_codes)
        permissions = $u.permissions
    }
}

function Get-DirectoryUsers {
    $list = @()
    foreach ($u in @($script:Payload.users)) {
        if (-not $u) { continue }
        if ($u.is_active -eq $false) { continue }
        $list += @{
            user_id = $u.user_id
            display_name = $u.display_name
            departments = @($u.departments)
            login_codes = @($u.login_codes)
        }
    }
    return @{ users = $list }
}

function Read-RequestBody($ctx) {
    $reader = New-Object System.IO.StreamReader($ctx.Request.InputStream, [System.Text.Encoding]::UTF8)
    try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}

function Write-JsonResponse($ctx, $statusCode, $obj, $extraHeaders) {
    $json = ConvertTo-JsonDeep $obj
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $ctx.Response.StatusCode = $statusCode
    $ctx.Response.ContentType = "application/json; charset=utf-8"
    $ctx.Response.ContentLength64 = $bytes.Length
    if ($extraHeaders) {
        foreach ($key in $extraHeaders.Keys) {
            $ctx.Response.Headers.Add([string]$key, [string]$extraHeaders[$key])
        }
    }
    $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $ctx.Response.Close()
}

function Write-BytesResponse($ctx, $statusCode, $contentType, $bytes) {
    $ctx.Response.StatusCode = $statusCode
    $ctx.Response.ContentType = $contentType
    $ctx.Response.ContentLength64 = $bytes.Length
    $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $ctx.Response.Close()
}

function Get-SessionUserId($ctx) {
    $cookie = $ctx.Request.Headers["Cookie"]
    if (-not $cookie) { return $null }
    foreach ($part in ($cookie -split ";")) {
        $kv = $part.Trim()
        if ($kv.StartsWith("wt_session=")) {
            $token = $kv.Substring(11)
            if ($script:Sessions.ContainsKey($token)) { return $script:Sessions[$token] }
        }
    }
    return $null
}

function Find-UserById($userId) {
    foreach ($u in @($script:Payload.users)) {
        if ($u.user_id -eq $userId) { return $u }
    }
    return $null
}

function Merge-PreservePins($incoming) {
    if (-not $incoming.users) { return $incoming }
    $oldById = @{}
    foreach ($u in @($script:Payload.users)) {
        if ($u -and $u.user_id) { $oldById[[string]$u.user_id] = $u }
    }
    foreach ($u in @($incoming.users)) {
        if (-not $u) { continue }
        $old = $oldById[[string]$u.user_id]
        if ($old -and (-not $u.pin) -and $old.pin) {
            $u | Add-Member -NotePropertyName pin -NotePropertyValue $old.pin -Force
        }
    }
    return $incoming
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($prefix)
try {
    $listener.Start()
} catch {
    Write-Output "FAILED to bind $prefix"
    Write-Output $_.Exception.Message
    Write-Output "Tip: close another process on $port, or run: netsh http add urlacl url=$prefix user=$env:USERNAME"
    exit 1
}

Write-Output "============================================================"
Write-Output "LOCAL DEMO: day-total checkbox (isolated, in-memory)"
Write-Output "URL:  http://127.0.0.1:$port/app.html#territoryMetrics"
Write-Output "User: Demo admin (local test)  /  PIN: 5555"
Write-Output "Root: $root"
Write-Output "DELETE demo files before publish."
Write-Output "============================================================"

while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    try {
        $method = $ctx.Request.HttpMethod.ToUpperInvariant()
        $path = $ctx.Request.Url.LocalPath
        if ($path -eq "/") { $path = "/index.html" }

        if ($path.StartsWith("/api/")) {
            if ($path -eq "/api/health" -and $method -eq "GET") {
                Write-JsonResponse $ctx 200 @{ ok = $true; demo = "day_total" }
                continue
            }
            if ($path -eq "/api/directory" -and $method -eq "GET") {
                Write-JsonResponse $ctx 200 (Get-DirectoryUsers)
                continue
            }
            if ($path -eq "/api/login" -and $method -eq "POST") {
                $body = Read-RequestBody $ctx
                $req = if ($body) { $body | ConvertFrom-Json } else { $null }
                $pin = [string]($(if ($req) { $req.pin } else { "" }))
                $userId = [string]($(if ($req) { $req.user_id } else { "" }))
                $user = $null
                if ($userId) {
                    $cand = Find-UserById $userId
                    if ($cand -and [string]$cand.pin -eq $pin -and $cand.is_active -ne $false) { $user = $cand }
                } else {
                    foreach ($u in @($script:Payload.users)) {
                        if ($u.role -eq "admin" -and [string]$u.pin -eq $pin -and $u.is_active -ne $false) {
                            $user = $u; break
                        }
                    }
                }
                if (-not $user) {
                    Write-JsonResponse $ctx 401 @{ error = "invalid login" }
                    continue
                }
                $token = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
                $script:Sessions[$token] = $user.user_id
                $cookie = "wt_session=$token; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400"
                Write-JsonResponse $ctx 200 @{ user = (Get-PublicUser $user) } @{ "Set-Cookie" = $cookie }
                continue
            }
            if ($path -eq "/api/logout" -and $method -eq "POST") {
                $cookieHdr = $ctx.Request.Headers["Cookie"]
                if ($cookieHdr) {
                    foreach ($part in ($cookieHdr -split ";")) {
                        $kv = $part.Trim()
                        if ($kv.StartsWith("wt_session=")) {
                            $token = $kv.Substring(11)
                            $script:Sessions.Remove($token) | Out-Null
                        }
                    }
                }
                Write-JsonResponse $ctx 200 @{ ok = $true } @{ "Set-Cookie" = "wt_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" }
                continue
            }
            if ($path -eq "/api/me" -and $method -eq "GET") {
                $uid = Get-SessionUserId $ctx
                $user = if ($uid) { Find-UserById $uid } else { $null }
                if (-not $user) {
                    Write-JsonResponse $ctx 401 @{ error = "unauthorized" }
                    continue
                }
                Write-JsonResponse $ctx 200 @{ user = (Get-PublicUser $user) }
                continue
            }
            if ($path -eq "/api/state" -and $method -eq "GET") {
                $uid = Get-SessionUserId $ctx
                if (-not $uid) {
                    Write-JsonResponse $ctx 401 @{ error = "unauthorized" }
                    continue
                }
                Write-JsonResponse $ctx 200 @{ version = $script:Version; payload = $script:Payload }
                continue
            }
            if ($path -eq "/api/state" -and $method -eq "PUT") {
                $uid = Get-SessionUserId $ctx
                if (-not $uid) {
                    Write-JsonResponse $ctx 401 @{ error = "unauthorized" }
                    continue
                }
                $body = Read-RequestBody $ctx
                $req = $body | ConvertFrom-Json
                $clientVer = [int]($(if ($null -ne $req.version) { $req.version } else { -1 }))
                if ($clientVer -ne $script:Version) {
                    Write-JsonResponse $ctx 409 @{ error = "version conflict"; version = $script:Version }
                    continue
                }
                if ($null -eq $req.payload) {
                    Write-JsonResponse $ctx 400 @{ error = "missing payload" }
                    continue
                }
                $script:Payload = Merge-PreservePins $req.payload
                $script:Version = $script:Version + 1
                Write-JsonResponse $ctx 200 @{ version = $script:Version; payload = $script:Payload }
                continue
            }
            Write-JsonResponse $ctx 404 @{ error = "not found" }
            continue
        }

        $rel = $path.TrimStart("/").Replace("/", "\")
        $file = Join-Path $root $rel
        if (-not (Test-Path -LiteralPath $file)) {
            $ctx.Response.StatusCode = 404
            $ctx.Response.Close()
            continue
        }
        $bytes = [System.IO.File]::ReadAllBytes($file)
        $ext = [System.IO.Path]::GetExtension($file).ToLowerInvariant()
        $ctype = "application/octet-stream"
        if ($ext -eq ".html") { $ctype = "text/html; charset=utf-8" }
        elseif ($ext -eq ".js") { $ctype = "application/javascript; charset=utf-8" }
        elseif ($ext -eq ".css") { $ctype = "text/css; charset=utf-8" }
        elseif ($ext -eq ".json") { $ctype = "application/json; charset=utf-8" }
        elseif ($ext -eq ".svg") { $ctype = "image/svg+xml" }
        elseif ($ext -eq ".png") { $ctype = "image/png" }
        elseif ($ext -eq ".ico") { $ctype = "image/x-icon" }
        try {
            Write-BytesResponse $ctx 200 $ctype $bytes
        } catch {
            try { $ctx.Response.Abort() } catch {}
        }
    } catch {
        try {
            Write-JsonResponse $ctx 500 @{ error = "demo server error"; detail = $_.Exception.Message }
        } catch {
            try { $ctx.Response.Abort() } catch {}
        }
    }
}
