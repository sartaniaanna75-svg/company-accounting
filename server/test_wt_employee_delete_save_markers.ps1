# Маркеры: значимые связи удаления + узкий OPS карточки без catalog items.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $root "app.html"
if (-not (Test-Path $appPath)) { $appPath = Join-Path $PSScriptRoot "..\app.html" }
$app = Get-Content -Raw -Encoding UTF8 $appPath
$api = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot "wt_api.py")

function Ok($cond, $msg) {
  if (-not $cond) { throw "FAIL: $msg" }
  Write-Host "OK $msg"
}

Ok ($app -match "function wtSalaryRecordMeaningful") "client salary meaningful"
Ok ($app -match "function wtAccessListMeaningful") "client access meaningful"
Ok ($app -match "function wtVacationPlanRowMeaningful") "client vacation meaningful"
Ok ($app -match "wtAccessListMeaningful\(mmap\[eid\]\)") "workAccess empty not block"
Ok ($app -match "employee_id") "employee_id based"
Ok ($app -match 'wtEmployeeDeleteBlockedToast\(e\.name, labels, eid\)') "toast passes employee_id"
Ok ($app -notmatch 'pushIfChanged\("shopMetricCatalog\.items"') "card OPS no catalog items"
Ok ($api -match "def _salary_record_meaningful") "server salary meaningful"
Ok ($api -match "def _access_list_meaningful") "server access meaningful"
Ok ($api -match "def _vacation_plan_row_meaningful") "server vacation meaningful"
Ok ($api -match "_access_list_meaningful\(mmap\.get\(eid\)\)") "server workAccess guard"
Ok ($app -match "wtSaveJobStatusFiredOnOps") "fired_on atomic OPS kept"

Write-Host "OK test_wt_employee_delete_save_markers"
