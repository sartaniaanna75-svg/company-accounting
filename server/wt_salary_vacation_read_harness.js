/**
 * Этап Б: чтение зарплаты/графика не материализует пустые записи.
 * Этап А: диагностика отпускных строго по employee_id.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

function normalizeSalaryRecord(rec) {
  const next = {
    fines: 0, bonus: 0, advance: 0, products: 0, skipFine: null,
    taxiDaily: 0, vacationPay: 0, vacationPaidEarlier: false, taxes: 0, issued: false
  };
  if (!rec || typeof rec !== "object") return next;
  next.fines = Number(rec.fines || 0);
  next.bonus = Number(rec.bonus || 0);
  next.advance = Number(rec.advance || 0);
  next.products = Number(rec.products || 0);
  next.taxiDaily = Number(rec.taxiDaily || 0);
  next.vacationPay = Number(rec.vacationPay || 0);
  next.vacationPaidEarlier = !!rec.vacationPaidEarlier;
  next.taxes = Number(rec.taxes || 0);
  next.issued = !!rec.issued;
  next.skipFine = rec.skipFine == null || rec.skipFine === "" ? null : Number(rec.skipFine);
  return next;
}

function peekSalaryMonth(salaryRoot, employeeId, month) {
  const bucket = salaryRoot && salaryRoot[month];
  if (!bucket || typeof bucket !== "object") return null;
  if (!Object.prototype.hasOwnProperty.call(bucket, employeeId)) return null;
  return normalizeSalaryRecord(bucket[employeeId]);
}

/** Просмотр ведомости: только peek/normalize, без записи ключа. */
function salaryViewRead(appData, employeeId, month) {
  const before = JSON.stringify(appData.salary || {});
  const peeked = peekSalaryMonth(appData.salary, employeeId, month);
  const calcAdj = normalizeSalaryRecord(
    appData.salary && appData.salary[month] && appData.salary[month][employeeId]
  );
  const after = JSON.stringify(appData.salary || {});
  return { peeked: peeked, calcAdj: calcAdj, mutated: before !== after };
}

function ensureSalaryMutates(appData, employeeId, month) {
  if (!appData.salary) appData.salary = {};
  if (!appData.salary[month]) appData.salary[month] = {};
  appData.salary[month][employeeId] = normalizeSalaryRecord(appData.salary[month][employeeId]);
  return appData.salary[month][employeeId];
}

function vacationPlanStoredYearList(root, year) {
  const list = root && root.vacationPlans && root.vacationPlans[year];
  return Array.isArray(list) ? list : [];
}

function findVacationPlanRecRead(root, employeeId, year) {
  const emp = String(employeeId || "");
  return vacationPlanStoredYearList(root, year).find(function (x) {
    return x && String(x.employee_id) === emp;
  }) || null;
}

function vacationPlanYearListWrite(root, year) {
  if (!root.vacationPlans || typeof root.vacationPlans !== "object") root.vacationPlans = {};
  if (!Array.isArray(root.vacationPlans[year])) root.vacationPlans[year] = [];
  return root.vacationPlans[year];
}

function buildEmployeeVacationPlanDiagRows(payload, employeeId, year) {
  const eid = String(employeeId || "").trim();
  const y = String(year || "").trim();
  const rows = [];
  if (!eid || !y || !payload) return rows;
  const list = payload.vacationPlans && payload.vacationPlans[y];
  if (!Array.isArray(list)) return rows;
  list.forEach(function (row) {
    if (!row || String(row.employee_id || "").trim() !== eid) return;
    rows.push({
      employee_id: eid,
      year: y,
      planned_date: String(row.planned_date || ""),
      planned_amount: row.planned_amount == null ? "" : row.planned_amount,
      status: String(row.status || ""),
      comment: String(row.comment || ""),
      history_count: Array.isArray(row.history) ? row.history.length : 0
    });
  });
  return rows;
}

function wtVacationPlanRowMeaningful(row) {
  if (!row || typeof row !== "object") return false;
  if (String(row.planned_date || "").trim()) return true;
  if (row.planned_amount != null && row.planned_amount !== "") {
    const n = Number(row.planned_amount);
    if (!Number.isFinite(n) || n !== 0) return true;
  }
  const st = String(row.status || "").trim().toLowerCase();
  if (st && st !== "planned") return true;
  if (String(row.comment || "").trim()) return true;
  return Array.isArray(row.history) && row.history.length > 0;
}

// --- salary view: no mass stubs ---
const salApp = { salary: { "2026-10": { "E1": { bonus: 100 } } } };
const r1 = salaryViewRead(salApp, "E1", "2026-10");
assert(!r1.mutated, "peek existing no mutate");
assert(r1.calcAdj.bonus === 100, "existing bonus visible");
const r2 = salaryViewRead(salApp, "E_NEW", "2026-10");
assert(!r2.mutated, "missing emp peek no mutate");
assert(!Object.prototype.hasOwnProperty.call(salApp.salary["2026-10"], "E_NEW"), "no stub key");
assert(r2.calcAdj.bonus === 0, "missing shows zeros via normalize");
const beforeMonth = JSON.stringify(salApp.salary);
salaryViewRead(salApp, "E1", "2026-11");
assert(JSON.stringify(salApp.salary) === beforeMonth, "other month peek no create month");
ensureSalaryMutates(salApp, "E_EDIT", "2026-10");
assert(Object.prototype.hasOwnProperty.call(salApp.salary["2026-10"], "E_EDIT"), "edit path still creates");

/** Эквивалентность отображения: ensureSalary перед calc vs только normalize (как calculateEmployee). */
function displayAdjAfterEnsure(appData, employeeId, month) {
  ensureSalaryMutates(appData, employeeId, month);
  return normalizeSalaryRecord(appData.salary[month][employeeId]);
}
function displayAdjWithoutEnsure(appData, employeeId, month) {
  return normalizeSalaryRecord(
    appData.salary && appData.salary[month] && appData.salary[month][employeeId]
  );
}
const eqMissing = { salary: { "2026-10": {} } };
const aMiss = displayAdjWithoutEnsure(eqMissing, "MISS", "2026-10");
const bMiss = displayAdjAfterEnsure(JSON.parse(JSON.stringify(eqMissing)), "MISS", "2026-10");
assert(JSON.stringify(aMiss) === JSON.stringify(bMiss), "missing salary display equal");
assert(!Object.prototype.hasOwnProperty.call(eqMissing.salary["2026-10"], "MISS"), "view path still no stub");
const eqExist = { salary: { "2026-10": { "E1": { bonus: 50, vacationPay: 200 } } } };
const aEx = displayAdjWithoutEnsure(eqExist, "E1", "2026-10");
const bEx = displayAdjAfterEnsure(JSON.parse(JSON.stringify(eqExist)), "E1", "2026-10");
assert(aEx.bonus === 50 && aEx.vacationPay === 200, "existing values without ensure");
assert(aEx.bonus === bEx.bonus && aEx.vacationPay === bEx.vacationPay, "existing display equal");

// --- vacation plan view: no empty year ---
const vpRoot = { vacationPlans: {} };
assert(findVacationPlanRecRead(vpRoot, "A", "2026") === null, "read miss");
assert(vpRoot.vacationPlans["2026"] === undefined, "view does not create year");
vacationPlanYearListWrite(vpRoot, "2026").push({
  employee_id: "A", planned_date: "2026-07-01", planned_amount: 500,
  status: "planned", comment: "", history: []
});
assert(findVacationPlanRecRead(vpRoot, "A", "2026").planned_date === "2026-07-01", "read existing");

// --- diag by employee_id only; same FIO not mixed ---
const payload = {
  workspaces: [{
    code: "office",
    employees: [
      { id: "T-O-000001", name: "Сартания Анна" },
      { id: "T-O-000002", name: "Сартания Анна" }
    ]
  }],
  vacationPlans: {
    "2026": [
      {
        employee_id: "T-O-000001",
        planned_date: "2026-08-01",
        planned_amount: 1200,
        status: "planned",
        comment: "x",
        history: [{ at: "1" }]
      },
      {
        employee_id: "T-O-000002",
        planned_date: "2026-09-01",
        planned_amount: 999,
        status: "moved",
        comment: "other",
        history: []
      }
    ]
  }
};
const diag = buildEmployeeVacationPlanDiagRows(payload, "T-O-000001", "2026");
assert(diag.length === 1, "one row for id");
assert(diag[0].planned_date === "2026-08-01", "date for target id");
assert(diag[0].planned_amount === 1200, "amount for target id");
assert(diag[0].history_count === 1, "history count");
assert(diag[0].comment === "x", "comment");
const diag2 = buildEmployeeVacationPlanDiagRows(payload, "T-O-000002", "2026");
assert(diag2.length === 1 && diag2[0].planned_amount === 999, "other id independent");
assert(wtVacationPlanRowMeaningful(payload.vacationPlans["2026"][0]), "real plan meaningful");
assert(!wtVacationPlanRowMeaningful({
  employee_id: "Z", planned_date: "", planned_amount: "", status: "planned", history: []
}), "empty stub not meaningful");

console.log("OK wt_salary_vacation_read_harness");
