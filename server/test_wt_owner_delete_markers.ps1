# Markers: WT_OWNER_USER_ID employee delete (owner only).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
$apiPath = Join-Path $PSScriptRoot "wt_api.py"
$envEx = Join-Path $root ".env.example"
$app = Get-Content -Raw -Encoding UTF8 $appPath
$api = Get-Content -Raw -Encoding UTF8 $apiPath
$envTxt = Get-Content -Raw -Encoding UTF8 $envEx

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($api -match "def configured_owner_user_id") "configured_owner_user_id"
Ok ($api -match "def is_payload_owner") "is_payload_owner"
Ok ($api -match "def public_session_user") "public_session_user"
Ok ($api -match "def employee_delete_owner_denied") "put owner denied helper"
Ok ($api -match "WT_OWNER_USER_ID") "env name in api"
Ok ($api -match "employee_delete_forbidden") "forbidden code"
Ok ($api -match "if not is_payload_owner\(user\):") "ops owner check"
Ok ($api -match "employee_delete_owner_denied\(user, incoming\)") "put owner check"
Ok ($api -match "employee_removal_block_message") "history guard kept"
Ok ($api -match "public_session_user\(user\)") "me/login session user"
Ok ($envTxt -match "WT_OWNER_USER_ID") "env.example documents owner"

Ok ($app -match "function canOwnerDeleteEmployee") "client canOwnerDelete"
Ok ($app -match "is_owner: user.is_owner === true") "rememberServerUser stores is_owner"
Ok ($app -match "canOwnerDeleteEmployee\(\)") "UI uses owner check"
Ok ($app -match "if \(!canOwnerDeleteEmployee\(\)\)") "deleteEmployee gates owner"
Ok ($app -match "employee_id:") "confirm shows employee_id"

# Delete button not tied only to canEditStaff in card actions
Ok ($app -match "function employeeCardActionsHtml") "card actions present"
$idx = $app.IndexOf("function employeeCardActionsHtml")
$slice = $app.Substring($idx, [Math]::Min(700, $app.Length - $idx))
Ok ($slice -match "canOwnerDeleteEmployee") "card actions owner delete"
Ok ($slice -match "canEditStaff") "card actions keep save for staff edit"

Write-Host "OK test_wt_owner_delete_markers"
