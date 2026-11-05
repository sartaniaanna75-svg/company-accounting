# Маркеры узкого сохранения меню (navLayouts) — без live БД.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "app.html"))) { $root = $PSScriptRoot }
$app = Get-Content -Raw -Encoding UTF8 (Join-Path $root "app.html")
$api = Get-Content -Raw -Encoding UTF8 (Join-Path $root "server\wt_api.py")

function Ok([bool]$cond, [string]$msg) {
    if (-not $cond) { throw "FAIL: $msg" }
    Write-Host "OK: $msg"
}

Ok ($app -match "function wtSaveNavLayoutOp") "helper wtSaveNavLayoutOp"
Ok ($app -match "function saveNavLayoutEditor") "saveNavLayoutEditor present"
Ok ($app -match "function resolveNavLayoutStorageKey") "resolveNavLayoutStorageKey present"
Ok ($app -match "function navLayoutStorageKey") "navLayoutStorageKey present"
Ok ($app -match "function navLayoutEditorScopeNote") "editor scope note present"
Ok ($app -match 'path = "navLayouts\." \+ key') "OPS path navLayouts.<code>"
Ok ($app -match "wtSaveNavLayoutOp\(key, nextLayout") "editor calls narrow saver"
Ok ($app -match "const key = navLayoutStorageKey\(\)") "save/apply use storage key"
Ok ($app -match "navLayoutEditorScopeNote\(key\)") "editor shows section scope"
Ok ($app -match "expected_v: typeof wtRecordVersion") "expected_v for conflict check"
Ok ($app -match "ok === true") "success only on confirmed ok"
Ok ($app -match "saveNavLayoutEditor\.busy") "busy guard"
Ok ($app -match "restoreIfStillOurs") "restore draft path on fail"
Ok ($app -match "forbidForcePut: true") "menu forbids broad PUT fallback"
Ok ($app -match "wtOpsUiEnabled\(true\)\) \{\s*\r?\n\s*if \(!options\.silentFail\)") "no-OPS refuses without saveApp"
Ok ($app -notmatch "function wtSaveNavLayoutOp\([\s\S]{0,900}?saveApp\(\)") "nav saver has no saveApp fallback"
Ok ($app -notmatch "function copyNavLayout|function migrateNavLayout|adminToSummary") "no auto admin->summary copy helper"
Ok ($app -match "return typeof isAppAdmin === `"function`" && isAppAdmin\(\);") "canConfigureNavLayout returns isAppAdmin"
# Тело canConfigureNavLayout без isTableAdmin / settings (только isAppAdmin).
$cfgIdx = $app.IndexOf("function canConfigureNavLayout")
Ok ($cfgIdx -ge 0) "canConfigureNavLayout present"
$cfgBody = if ($cfgIdx -ge 0) { $app.Substring($cfgIdx, [Math]::Min(350, $app.Length - $cfgIdx)) } else { "" }
Ok ($cfgBody -match "isAppAdmin") "canConfigureNavLayout uses isAppAdmin"
Ok ($cfgBody -notmatch "isTableAdmin") "canConfigureNavLayout no isTableAdmin"
Ok ($cfgBody -notmatch "userHasPerm\([^\)]*settings") "canConfigureNavLayout no settings perm check"
Ok ($api -match '"navLayouts"') "server OPS allowlist has navLayouts"
Ok ($api -match 'if top == "navLayouts"') "server ACL for navLayouts"
Ok ($api -match "def filter_nav_layouts_for_user\(") "filter_nav_layouts_for_user present"
Ok ($api -match "nav_layouts = filter_nav_layouts_for_user\(payload, user\)") "filter_state_payload includes navLayouts"
Ok ($api -match 'out\["navLayouts"\] = nav_layouts') "staff payload gets navLayouts"
$navIdx = $api.IndexOf('if top == "navLayouts"')
Ok ($navIdx -ge 0) "navLayouts ACL block present"
$navBody = if ($navIdx -ge 0) { $api.Substring($navIdx, [Math]::Min(220, $api.Length - $navIdx)) } else { "" }
Ok ($navBody -match "is_app_admin\(user\)") "navLayouts OPS only is_app_admin"
Ok ($navBody -notmatch 'user_has_perm\(user, "settings"') "navLayouts OPS not settings:edit"

$tests = Get-Content -Raw -Encoding UTF8 (Join-Path $root "server\test_wt_api.py")
Ok ($tests -match "test_get_state_admin_receives_all_nav_layouts") "test admin GET navLayouts"
Ok ($tests -match "test_get_state_shop_staff_receives_only_shop_nav_layout") "test shop staff GET navLayouts"
Ok ($tests -match "test_get_state_multi_dept_staff_receives_own_nav_layouts_only") "test multi-dept GET navLayouts"
Ok ($tests -match "test_nav_layout_relative_order_kept_for_subset_rights") "test relative order subset rights"

$harness = Join-Path $PSScriptRoot "wt_nav_layout_key_harness.js"
Ok (Test-Path $harness) "key harness file exists"
$nodeCandidates = @(
    (Get-Command node -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    "$env:LOCALAPPDATA\Programs\cursor\resources\app\resources\helpers\node.exe",
    "$env:LOCALAPPDATA\Programs\Cursor\resources\app\resources\helpers\node.exe",
    "$env:ProgramFiles\nodejs\node.exe"
) | Where-Object { $_ -and (Test-Path $_) }
$nodeExe = if ($nodeCandidates) { $nodeCandidates[0] } else { $null }
if ($nodeExe) {
    & $nodeExe $harness
    if ($LASTEXITCODE -ne 0) { throw "FAIL: wt_nav_layout_key_harness.js" }
    Write-Host "OK: wt_nav_layout_key_harness.js"
    $saveHarness = Join-Path $PSScriptRoot "wt_nav_layout_save_harness.js"
    if (Test-Path $saveHarness) {
        & $nodeExe $saveHarness
        if ($LASTEXITCODE -ne 0) { throw "FAIL: wt_nav_layout_save_harness.js" }
        Write-Host "OK: wt_nav_layout_save_harness.js"
    }
    $aclHarness = Join-Path $PSScriptRoot "wt_nav_layout_acl_harness.js"
    if (Test-Path $aclHarness) {
        & $nodeExe $aclHarness
        if ($LASTEXITCODE -ne 0) { throw "FAIL: wt_nav_layout_acl_harness.js" }
        Write-Host "OK: wt_nav_layout_acl_harness.js"
    }
    $deliveryHarness = Join-Path $PSScriptRoot "wt_nav_layout_delivery_harness.js"
    if (Test-Path $deliveryHarness) {
        & $nodeExe $deliveryHarness
        if ($LASTEXITCODE -ne 0) { throw "FAIL: wt_nav_layout_delivery_harness.js" }
        Write-Host "OK: wt_nav_layout_delivery_harness.js"
    }
} else {
    Write-Host "SKIP: node not found for key harness"
}

Write-Host "All nav layout OPS markers OK."
