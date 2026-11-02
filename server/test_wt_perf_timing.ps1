# WorkTime perf timing markers (opt-in localStorage.wtPerfTiming). Run:
# powershell -NoProfile -ExecutionPolicy Bypass -File server/test_wt_perf_timing.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding $false
$src = [IO.File]::ReadAllText((Join-Path $Root "app.html"), $utf8)
$passed = 0; $failed = 0
function Ok($c, $m) {
    if ($c) { $script:passed++; Write-Host "  OK  $m" }
    else { $script:failed++; Write-Host "  FAIL  $m" -ForegroundColor Red }
}

Write-Host "=== wtPerfTiming markers ==="
Ok ($src.Contains("function wtPerfTimingEnabled(")) "wtPerfTimingEnabled"
Ok ($src.Contains('localStorage.getItem("wtPerfTiming") === "1"')) "flag wtPerfTiming=1"
Ok ($src.Contains("function wtPerfMark(")) "wtPerfMark"
Ok ($src.Contains("function wtPerfConflict(")) "wtPerfConflict"
Ok ($src.Contains("[wt-perf]")) "console prefix wt-perf"

# Existing save timing preserved
Ok ($src.Contains("function wtSaveTimingEnabled(")) "wtSaveTiming kept"
Ok ($src.Contains('localStorage.getItem("wtSaveTiming") === "1"')) "flag wtSaveTiming=1"
Ok ($src.Contains('console.info("[wt-save]"')) "save console prefix"

# Boot / SoftSync / UI stages
@(
    "boot.state.network",
    "boot.apply",
    "boot.firstUi",
    "boot.workspaceUi",
    "soft.version.network",
    "soft.state.network",
    "soft.apply",
    "soft.refreshAll",
    "ui.showSection",
    "ui.refreshAll",
    "ui.renderShopMetrics"
) | ForEach-Object { Ok ($src.Contains($_)) "stage $_" }

# Conflict diagnostics must not log payload / updated_by
$conf = $src.IndexOf("function wtPerfConflict(")
$confSlice = $src.Substring($conf, [Math]::Min(900, $src.Length - $conf))
Ok ($confSlice.Contains("localVersion")) "conflict logs localVersion"
Ok ($confSlice.Contains("serverVersion")) "conflict logs serverVersion"
Ok ($confSlice.IndexOf("updated_by") -lt 0) "conflict omits updated_by"
Ok ($confSlice.IndexOf("payload") -lt 0) "conflict helper omits payload"

# Must not auto-clear conflict by rewriting server blindly in perf helpers
Ok ($confSlice.IndexOf("applyServerState") -lt 0) "perf conflict helper no applyServerState"
Ok ($confSlice.IndexOf("wtStateVersion =") -lt 0) "perf conflict helper no version rewrite"

# PUT path still calls handleWtSaveConflict (business flow unchanged)
$put = $src.IndexOf("function putServerStateOnce(")
$putSlice = $src.Substring($put, [Math]::Min(5500, $src.Length - $put))
Ok ($putSlice.Contains("handleWtSaveConflict")) "PUT still uses handleWtSaveConflict"
Ok ($putSlice.Contains("wtPerfConflict")) "PUT 409 reports wtPerfConflict"
Ok ($putSlice.Contains("wtSaveMark")) "PUT keeps wtSaveMark"

Write-Host ""
Write-Host ("=== Result: passed={0} failed={1} ===" -f $passed, $failed)
if ($failed -gt 0) { exit 1 }
Write-Host "All test_wt_perf_timing.ps1 checks passed."
exit 0
