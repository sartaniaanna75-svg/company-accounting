/**
 * Архив сотрудников: только UI-фильтр по jobStatus, без копий и без мутации payload.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

function normalizeSummaryStaffListMode(mode) {
  const m = String(mode || "").trim();
  if (m === "archive" || m === "all") return m;
  return "active";
}

function employeeMatchesSummaryStaffListMode(employee, mode) {
  const m = normalizeSummaryStaffListMode(mode);
  const fired = !!(employee && employee.jobStatus === "fired");
  if (m === "archive") return fired;
  if (m === "all") return true;
  return !fired;
}

function summaryDirectoryGroupsFilter(workspaces, opts) {
  opts = opts || {};
  const listMode = normalizeSummaryStaffListMode(opts.listMode);
  const deptFilter = opts.deptFilter || "";
  const q = String(opts.search || "").toLowerCase().replace(/\s+/g, " ").trim();
  const groups = [];
  (workspaces || []).forEach(function (w) {
    if (!w || !w.code || w.type === "summary") return;
    if (deptFilter && w.code !== deptFilter) return;
    const people = (w.employees || []).filter(function (employee) {
      if (!employee || !employee.id) return false;
      if (!employeeMatchesSummaryStaffListMode(employee, listMode)) return false;
      if (q) {
        const name = String(employee.name || "").toLowerCase();
        if (name.indexOf(q) === -1 && String(employee.id || "").toLowerCase().indexOf(q) === -1) {
          return false;
        }
      }
      return true;
    });
    if (people.length) groups.push({ code: w.code, employees: people });
  });
  return groups;
}

const ws = [
  {
    code: "office",
    employees: [
      { id: "T-O-000001", name: "Сартания Анна", jobStatus: "fired", fired_on: "2026-10-15", position: "Менеджер" },
      { id: "T-O-000002", name: "Сартания Анна", jobStatus: "working", position: "Менеджер" },
      { id: "T-O-000003", name: "Иванов Иван", jobStatus: "working", position: "Офис" }
    ]
  },
  {
    code: "shop",
    employees: [
      { id: "V-000001", name: "Петров", jobStatus: "fired", fired_on: "2026-09-01" }
    ]
  }
];

const snap = JSON.stringify(ws);

assert(normalizeSummaryStaffListMode("") === "active", "default active");
assert(normalizeSummaryStaffListMode("archive") === "archive", "archive mode");
assert(normalizeSummaryStaffListMode("all") === "all", "all mode");

const active = summaryDirectoryGroupsFilter(ws, { listMode: "active" });
const activeIds = active.reduce(function (a, g) {
  return a.concat(g.employees.map(function (e) { return e.id; }));
}, []);
assert(activeIds.indexOf("T-O-000002") !== -1, "working in active");
assert(activeIds.indexOf("T-O-000003") !== -1, "working2 in active");
assert(activeIds.indexOf("T-O-000001") === -1, "fired not in active");
assert(activeIds.indexOf("V-000001") === -1, "shop fired not in active");

const archive = summaryDirectoryGroupsFilter(ws, { listMode: "archive" });
const archIds = archive.reduce(function (a, g) {
  return a.concat(g.employees.map(function (e) { return e.id; }));
}, []);
assert(archIds.indexOf("T-O-000001") !== -1, "fired in archive");
assert(archIds.indexOf("V-000001") !== -1, "shop fired in archive");
assert(archIds.indexOf("T-O-000002") === -1, "working not in archive");

const all = summaryDirectoryGroupsFilter(ws, { listMode: "all" });
const allIds = all.reduce(function (a, g) {
  return a.concat(g.employees.map(function (e) { return e.id; }));
}, []);
assert(allIds.length === 4, "all four cards");
assert(allIds.indexOf("T-O-000001") !== -1 && allIds.indexOf("T-O-000002") !== -1, "same FIO distinct ids");

const officeArch = summaryDirectoryGroupsFilter(ws, { listMode: "archive", deptFilter: "office" });
assert(officeArch.length === 1 && officeArch[0].employees.length === 1, "dept filter + archive");
assert(officeArch[0].employees[0].id === "T-O-000001", "office fired only");

const search = summaryDirectoryGroupsFilter(ws, { listMode: "all", search: "сартания" });
const searchIds = search[0].employees.map(function (e) { return e.id; }).sort();
assert(searchIds.join(",") === "T-O-000001,T-O-000002", "search keeps both same FIO");

// Переключение вкладок не мутирует workspaces
summaryDirectoryGroupsFilter(ws, { listMode: "active" });
summaryDirectoryGroupsFilter(ws, { listMode: "archive" });
summaryDirectoryGroupsFilter(ws, { listMode: "all" });
assert(JSON.stringify(ws) === snap, "filter does not mutate appData");

// Исторические хуки не подменяем этим фильтром (контракт имён)
assert(typeof employeeMatchesSummaryStaffListMode === "function", "staff list matcher");

console.log("OK wt_staff_archive_tabs_harness");
