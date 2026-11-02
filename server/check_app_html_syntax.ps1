# Parse-check all inline <script> blocks in app.html (no execution of app logic).
# Run before publish:
#   powershell -NoProfile -ExecutionPolicy Bypass -File server/check_app_html_syntax.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$htmlPath = Join-Path $Root "app.html"
if (-not (Test-Path $htmlPath)) { throw "app.html not found: $htmlPath" }

function Find-NodeExe {
    $candidates = @(
        (Get-Command node -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
        "$env:LOCALAPPDATA\Programs\cursor\resources\app\resources\helpers\node.exe",
        "$env:LOCALAPPDATA\Programs\Cursor\resources\app\resources\helpers\node.exe",
        "$env:ProgramFiles\nodejs\node.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
    if ($candidates) { return $candidates[0] }
    return $null
}

$node = Find-NodeExe
if (-not $node) {
    Write-Host "FAIL: node.exe not found (need Node or Cursor helper node for syntax check)." -ForegroundColor Red
    exit 2
}

$tmpDir = Join-Path $env:TEMP ("wt-app-syntax-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmpDir | Out-Null
$extractJs = @'
const fs = require("fs");
const path = require("path");
const htmlPath = process.argv[2];
const outPath = process.argv[3];
const html = fs.readFileSync(htmlPath, "utf8");
const re = /<script(?![^>]*\bsrc=)([^>]*)>([\s\S]*?)<\/script>/gi;
const parts = [];
let m;
while ((m = re.exec(html))) {
  const attrs = String(m[1] || "").toLowerCase();
  if (attrs.indexOf("text/template") !== -1) continue;
  if (attrs.indexOf("application/json") !== -1) continue;
  parts.push(m[2]);
}
if (!parts.length) {
  console.error("SYNTAX_ERR no inline script blocks found");
  process.exit(1);
}
fs.writeFileSync(outPath, parts.join("\n;\n"), "utf8");
console.log("EXTRACTED_BLOCKS=" + parts.length);
'@
$checkJs = @'
const fs = require("fs");
const vm = require("vm");
const srcPath = process.argv[2];
const src = fs.readFileSync(srcPath, "utf8");
try {
  new vm.Script(src, { filename: "app.html#inline-scripts" });
  console.log("SYNTAX_OK");
} catch (e) {
  console.error("SYNTAX_ERR " + (e && e.name ? e.name : "Error") + ": " + (e && e.message ? e.message : e));
  process.exit(1);
}
'@
$extractPath = Join-Path $tmpDir "extract.js"
$checkPath = Join-Path $tmpDir "check.js"
$bundlePath = Join-Path $tmpDir "app_inline.js"
Set-Content -Path $extractPath -Value $extractJs -Encoding UTF8
Set-Content -Path $checkPath -Value $checkJs -Encoding UTF8

try {
    Write-Host "Node: $node"
    & $node $extractPath $htmlPath $bundlePath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $node $checkPath $bundlePath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "app.html inline JavaScript: syntax OK"
    exit 0
} finally {
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
}
