/**
 * Зеркало server employee_linked_history_labels (значимые связи / пустые заглушки).
 */
"use strict";

function salaryMeaningful(rec) {
  if (!rec || typeof rec !== "object") return !!rec;
  const keys = ["fines", "bonus", "advance", "products", "taxiDaily", "vacationPay", "taxes", "reflect1c", "expenseZp"];
  if (keys.some(function (k) { return Number(rec[k] || 0) !== 0; })) return true;
  if (rec.issued === true || rec.vacationPaidEarlier === true) return true;
  if (rec.skipFine != null && rec.skipFine !== "") return true;
  if (String(rec.updated_at || "").trim() || String(rec.external_1c_id || "").trim()) return true;
  return !!(rec.sources && typeof rec.sources === "object" && Object.keys(rec.sources).length);
}
function vacationMeaningful(row) {
  if (!row || typeof row !== "object") return false;
  if (String(row.planned_date || "").trim()) return true;
  if (row.planned_amount != null && row.planned_amount !== "" && Number(row.planned_amount) !== 0) return true;
  const st = String(row.status || "").trim().toLowerCase();
  if (st && st !== "planned") return true;
  if (String(row.comment || "").trim()) return true;
  return Array.isArray(row.history) && row.history.length > 0;
}
function accessMeaningful(val) {
  if (Array.isArray(val)) return val.some(function (x) { return String(x || "").trim(); });
  return !!(val && typeof val === "object" && Object.keys(val).length);
}
function labelsFor(payload, eid) {
  const out = [];
  const salary = payload.salary || {};
  Object.keys(salary).forEach(function (month) {
    const bucket = salary[month];
    if (bucket && Object.prototype.hasOwnProperty.call(bucket, eid) && salaryMeaningful(bucket[eid])) {
      out.push("зарплата (" + month + ")");
    }
  });
  const wa = (((payload.shopMetrics || {}).workAccess) || {});
  Object.keys(wa).forEach(function (month) {
    const mmap = wa[month] || {};
    if (Object.prototype.hasOwnProperty.call(mmap, eid) && accessMeaningful(mmap[eid])) {
      out.push("назначения метрик");
    }
  });
  const vp = payload.vacationPlans || {};
  Object.keys(vp).forEach(function (year) {
    (vp[year] || []).forEach(function (row) {
      if (row && String(row.employee_id) === eid && vacationMeaningful(row)) {
        out.push("график отпусков (" + year + ")");
      }
    });
  });
  ((payload.workspaces) || []).forEach(function (w) {
    const ts = w.timesheet || {};
    Object.keys(ts).forEach(function (month) {
      const days = ts[month] && ts[month][eid];
      if (!days) return;
      const has = Object.keys(days).some(function (d) {
        return days[d] && String(days[d].status || "").trim();
      });
      if (has) out.push("табель");
    });
  });
  return out;
}
function assert(c, m) { if (!c) throw new Error(m); }

const stubs = {
  workspaces: [{ code: "office", employees: [{ id: "A", name: "Same" }, { id: "B", name: "Same" }], timesheet: {} }],
  salary: { "2026-10": { A: {} } },
  shopMetrics: { workAccess: { "2026-11": { A: [] } }, extraAccess: { A: [] } },
  vacationPlans: { "2026": [{ employee_id: "A", planned_date: "", planned_amount: "", status: "planned", history: [] }] }
};
assert(labelsFor(stubs, "A").length === 0, "stubs allow delete A");
assert(labelsFor(stubs, "B").length === 0, "same FIO other id independent");

const real = {
  workspaces: [{ code: "office", timesheet: { "2026-10": { A: { "1": { status: "Я" } } } } }],
  salary: { "2026-10": { A: { bonus: 10 } } },
  shopMetrics: { workAccess: { "2026-11": { A: ["m1"] } } },
  vacationPlans: { "2026": [{ employee_id: "A", planned_date: "2026-07-01", planned_amount: 1, status: "planned", history: [] }] }
};
const L = labelsFor(real, "A");
assert(L.some(function (x) { return x.indexOf("зарплата") === 0; }), "salary blocks");
assert(L.indexOf("назначения метрик") !== -1, "metrics block");
assert(L.some(function (x) { return x.indexOf("график") === 0; }), "vacation blocks");
assert(L.indexOf("табель") !== -1, "timesheet blocks");

console.log("OK wt_employee_linked_labels_harness");
