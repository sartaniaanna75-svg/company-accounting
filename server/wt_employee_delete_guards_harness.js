/**
 * Зеркало клиентских guard'ов удаления: пустые заглушки vs значимые связи.
 */
"use strict";

function wtSalaryRecordMeaningful(rec) {
  if (rec == null) return false;
  if (!rec || typeof rec !== "object") return !!rec;
  const moneyKeys = ["fines", "bonus", "advance", "products", "taxiDaily", "vacationPay", "taxes", "reflect1c", "expenseZp"];
  if (moneyKeys.some(function (k) { return Number(rec[k] || 0) !== 0; })) return true;
  if (rec.issued === true) return true;
  if (rec.vacationPaidEarlier === true) return true;
  if (rec.skipFine != null && rec.skipFine !== "") return true;
  if (String(rec.updated_at || "").trim()) return true;
  if (String(rec.external_1c_id || "").trim()) return true;
  if (rec.sources && typeof rec.sources === "object" && Object.keys(rec.sources).length) return true;
  return false;
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

function wtAccessListMeaningful(val) {
  if (val == null) return false;
  if (Array.isArray(val)) return val.some(function (x) { return String(x || "").trim(); });
  if (typeof val === "object") return Object.keys(val).length > 0;
  return !!val;
}

function assert(c, m) { if (!c) throw new Error(m); }

assert(!wtSalaryRecordMeaningful({}), "empty salary");
assert(!wtSalaryRecordMeaningful({ bonus: 0, issued: false }), "zero salary");
assert(wtSalaryRecordMeaningful({ bonus: 100 }), "bonus salary");
assert(!wtAccessListMeaningful([]), "empty workAccess");
assert(wtAccessListMeaningful(["m1"]), "real workAccess");
assert(!wtVacationPlanRowMeaningful({ employee_id: "a", planned_date: "", status: "planned", history: [] }), "empty vacation");
assert(wtVacationPlanRowMeaningful({ employee_id: "a", planned_date: "2026-07-01" }), "dated vacation");

console.log("OK wt_employee_delete_guards_harness");
