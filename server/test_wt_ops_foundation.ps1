# Foundation markers for multi-user point ops. Run:
# powershell -NoProfile -ExecutionPolicy Bypass -File server/test_wt_ops_foundation.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding $false
$api = [IO.File]::ReadAllText((Join-Path $Root "server\wt_api.py"), $utf8)
$html = [IO.File]::ReadAllText((Join-Path $Root "app.html"), $utf8)
$tests = [IO.File]::ReadAllText((Join-Path $Root "server\test_wt_api.py"), $utf8)
$passed = 0; $failed = 0
function Ok($c, $m) {
    if ($c) { $script:passed++; Write-Host "  OK  $m" }
    else { $script:failed++; Write-Host "  FAIL  $m" -ForegroundColor Red }
}

Write-Host "=== Server ops API ==="
Ok ($api.Contains("def handle_post_state_op(")) "handle_post_state_op"
Ok ($api.Contains('path == "/api/state/op"')) "route POST /api/state/op"
Ok ($api.Contains("def apply_state_op(")) "apply_state_op"
Ok ($api.Contains("def protect_wt_records_on_put(")) "protect_wt_records_on_put"
Ok ($api.Contains("def ops_enabled(")) "ops_enabled flag"
Ok ($api.Contains("WT_OPS_ENABLED")) "env WT_OPS_ENABLED"
Ok ($api.Contains("protect_wt_records_on_put(current, stored, incoming)")) "PUT uses protect"
Ok ($api.Contains('"ops": ops_enabled()')) "health exposes ops"
Ok ($api.Contains('"error": "conflict"') -and $api.Contains('"server_value"')) "conflict returns server_value"
Ok ($api.Contains("remember_idempotency")) "idempotency store"
Ok ($api.Contains("fetch_state_for_update")) "ops use row lock"

Write-Host "=== Client foundation ==="
Ok ($html.Contains("function wtSaveRecordOp(")) "wtSaveRecordOp"
Ok ($html.Contains("function wtResolveRecordConflict(")) "wtResolveRecordConflict"
Ok ($html.Contains('localStorage.getItem("wtOps") === "1"')) "UI flag off by default"
Ok ($html.Contains('"/api/state/op"')) "client posts to /api/state/op"
Ok ($html.Contains("WT_RECORDS_KEY")) "client record map key"
Ok ($html.Contains("out[WT_RECORDS_KEY] = appData[WT_RECORDS_KEY]")) "staff PUT sends record map"
Ok ($html.Contains("function wtCellSaveStatusAttr(")) "status attr helper"

Write-Host "=== Automated tests present ==="
Ok ($tests.Contains("class WtOpsFoundationTests")) "WtOpsFoundationTests class"
Ok ($tests.Contains("test_op_real_conflict_same_path")) "conflict test"
Ok ($tests.Contains("test_stale_put_does_not_wipe_op_value")) "stale PUT protect test"
Ok ($tests.Contains("test_op_idempotency")) "idempotency test"

Write-Host "=== Safety ==="
Ok (-not ($api -match "if expected != current_version:\s*\n\s*pass")) "global version check not removed"
Ok ($api.Contains("if expected != current_version:")) "PUT still checks global version"
Ok ($api.Contains("def _ops_metric_branch_allowed(")) "metric branch ACL helper"
Ok ($api.Contains("client_v > server_v")) "protect rejects forged client_v"
Ok ($api.Contains("def _workspace_by_code(")) "workspaces path by code"
Ok ($api.Contains('out[WT_RECORDS_KEY] = _clone(cur_map)')) "PUT keeps server record map only"
Ok ($api.Contains("def sync_record_versions_after_put(")) "PUT syncs record_v after changes"
Ok ($api.Contains("sync_record_versions_after_put(current, stored, user)")) "prepare_put calls sync"
Ok ($api.Contains("def _collect_changed_op_paths(")) "changed op paths collector"
Ok ($tests.Contains("test_staff_cannot_op_metric_setup_via_path")) "ACL setup path test"
Ok ($tests.Contains("test_forged_client_record_v_cannot_wipe_op")) "forged version test"
Ok ($tests.Contains("test_workspaces_op_by_code_keeps_list")) "workspaces list test"
Ok ($tests.Contains("test_ops_disabled_keeps_saved_values_for_put")) "disable keeps values test"
Ok ($tests.Contains("test_put_bumps_record_v_so_stale_op_conflicts")) "PUT bumps then stale op conflicts"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_wt_ops_foundation.ps1 checks passed."
Write-Host "NOTE: Run server/run_wt_ops_tests.ps1 for WtOpsFoundationTests (portable .tools/python-embed)."
Write-Host "NOTE: Run server/test_wt_ops_logic.ps1 for isolated scenario logic checks."
exit 0
