/**
 * Октябрь 2026: уволенный снят с «В табель» в ноябре — остаётся в общей зарплате октября.
 * Зеркало отбора salaryEmployeeGroups + includeInMonthPayroll (без формул оклада/KPI).
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

function salaryEmployeeGroups(ws, monthTimesheet) {
  const people = [];
  const seen = {};
  function add(e) {
    if (!e || !e.id || e.id === "self_checkout" || seen[e.id]) return;
    if (!includeInMonthPayroll(e, monthTimesheet)) return;
    seen[e.id] = true;
    people.push(e);
  }
  (ws.employeeIds || []).forEach(function (id) {
    add((ws.employees || []).find(function (e) { return e && e.id === id; }));
  });
  (ws.employees || []).forEach(add);
  return people;
}

function assert(c, m) { if (!c) throw new Error(m); }

// Октябрь: 5 человек в карточках; один уволен и снят с employeeIds (как после ноября).
const octoberTs = {
  e1: { "1": { status: "Я" } },
  e2: { "1": { status: "Я" } },
  e3: { "1": { status: "Я" } },
  e4: { "1": { status: "Я" } },
  e5: { "5": { status: "Я" }, "10": { status: "Я" } } // уволенный имел октябрьский табель
};

const shop = {
  code: "shop",
  employeeIds: ["e1", "e2", "e3", "e4"], // e5 снят с «В табель»
  employees: [
    { id: "e1", name: "A", jobStatus: "working" },
    { id: "e2", name: "B", jobStatus: "working" },
    { id: "e3", name: "C", jobStatus: "working" },
    { id: "e4", name: "D", jobStatus: "working" },
    { id: "e5", name: "Fired", jobStatus: "fired" }
  ]
};

const people = salaryEmployeeGroups(shop, octoberTs);
assert(people.length === 5, "october keeps 5 including fired without employeeIds");
assert(people.filter(function (e) { return e.id === "e5"; }).length === 1, "fired once, no duplicate");
assert(people.some(function (e) { return e.id === "e5"; }), "fired with october activity included");

// Ноябрь без активности уволенного — только как контроль отбора (полной проверки ЗП нет).
const novemberTs = {
  e1: { "1": { status: "Я" } },
  e2: { "1": { status: "Я" } },
  e3: { "1": { status: "Я" } },
  e4: { "1": { status: "Я" } }
};
const novPeople = salaryEmployeeGroups(shop, novemberTs);
assert(!novPeople.some(function (e) { return e.id === "e5"; }), "november without activity excludes fired");
assert(novPeople.length === 4, "november has 4");

console.log("OK wt_salary_october_roster_harness");
