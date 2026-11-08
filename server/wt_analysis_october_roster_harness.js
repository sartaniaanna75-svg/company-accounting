/**
 * Анализ / departmentMonthTotals: октябрь 2026 после снятия уволенного с «В табель».
 * Зеркало отбора (без формул calculateEmployee).
 */
"use strict";

function isWorking(e) { return !e || e.jobStatus !== "fired"; }

function employeeHasMonthActivity(empId, monthTimesheet) {
  const days = (monthTimesheet && monthTimesheet[empId]) || {};
  return Object.keys(days).some(function (d) {
    const st = days[d] && days[d].status;
    return !!(st && String(st).trim());
  });
}

function includeInMonthPayroll(e, monthTimesheet) {
  if (isWorking(e)) return true;
  return employeeHasMonthActivity(e.id, monthTimesheet);
}

function isSalesRep(e) {
  return !!(e && /торгов/i.test(String(e.position || "")));
}

function departmentMonthPeople(w, monthTimesheet) {
  const people = [];
  const seen = {};
  function add(e) {
    if (!e || !e.id || e.id === "self_checkout" || seen[e.id]) return;
    if (!includeInMonthPayroll(e, monthTimesheet)) return;
    if (isSalesRep(e) && w.code !== "drivers") return;
    seen[e.id] = true;
    people.push(e);
  }
  (w.employeeIds || []).forEach(function (id) {
    add((w.employees || []).find(function (e) { return e && e.id === id; }));
  });
  (w.employees || []).forEach(add);
  return people;
}

function assert(c, m) { if (!c) throw new Error(m); }

const octoberTs = {
  e1: { "1": { status: "Я" } },
  e2: { "1": { status: "Я" } },
  e3: { "1": { status: "Я" } },
  e4: { "1": { status: "Я" } },
  e5: { "5": { status: "Я" } }
};

const shop = {
  code: "shop",
  employeeIds: ["e1", "e2", "e3", "e4"],
  employees: [
    { id: "e1", name: "A", jobStatus: "working" },
    { id: "e2", name: "B", jobStatus: "working" },
    { id: "e3", name: "C", jobStatus: "working" },
    { id: "e4", name: "D", jobStatus: "working" },
    { id: "e5", name: "Fired", jobStatus: "fired" }
  ]
};

const octPeople = departmentMonthPeople(shop, octoberTs);
assert(octPeople.length === 5, "october dept row keeps 5");
assert(octPeople.filter(function (e) { return e.id === "e5"; }).length === 1, "no duplicate fired");

// Начисления: 4×20000 + 20000 = 100000 (модель сценария).
const payById = { e1: 20000, e2: 20000, e3: 20000, e4: 20000, e5: 20000 };
const accrued = octPeople.reduce(function (s, e) { return s + (payById[e.id] || 0); }, 0);
assert(accrued === 100000, "october dept accrued 100000 not 80000");

const novemberTs = {
  e1: { "1": { status: "Я" } },
  e2: { "1": { status: "Я" } },
  e3: { "1": { status: "Я" } },
  e4: { "1": { status: "Я" } }
};
const novPeople = departmentMonthPeople(shop, novemberTs);
assert(novPeople.length === 4, "november excludes fired without activity");
assert(!novPeople.some(function (e) { return e.id === "e5"; }), "fired not in november");

console.log("OK wt_analysis_october_roster_harness");
