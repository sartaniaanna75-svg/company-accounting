/**
 * Возвраты: отображение чисел черновика + фильтры author/cashier + orphan selected.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function crFormatPlainNumber(value) {
  if (value == null || value === "") return "";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (Object.is(n, -0) || n === 0) return "0";
  let s;
  if (Math.abs(n - Math.round(n)) < 1e-9) {
    s = String(Math.round(n));
  } else {
    s = n.toFixed(6).replace(/\.?0+$/, "");
  }
  return s.replace(".", ",");
}

function employeeCardWorkGroupId(employee) {
  return String(employee.work_tab_id || employee.work_group_id || "");
}

function employeeWorkGroupIds(employee) {
  const ids = [];
  function add(id) {
    const s = String(id || "").trim();
    if (s && ids.indexOf(s) === -1) ids.push(s);
  }
  (employee.work_group_ids || []).forEach(add);
  add(employee.work_tab_id);
  add(employee.work_group_id);
  return ids;
}

function employeeMatchesWorkTab(employee, tab) {
  return employeeWorkGroupIds(employee).indexOf(tab) !== -1;
}

function isTerritoryEmployee(e) {
  return e && e._territory === true;
}

function crEmpFitsKind(e, kind) {
  if (!e || !isTerritoryEmployee(e)) return false;
  const home = e.home;
  if (kind === "cashier") {
    return home === "warehouse" && employeeCardWorkGroupId(e) === "cashiers";
  }
  if (kind === "author") {
    if (home === "drivers" && employeeMatchesWorkTab(e, "sales")) return true;
    return e.can_cover_sales_reps === true;
  }
  return false;
}

function crEmpOptionsSelected(people, selected, kind) {
  const ids = [];
  const seen = {};
  people.forEach(function (e) {
    if (!crEmpFitsKind(e, kind)) return;
    if (seen[e.id]) return;
    seen[e.id] = true;
    ids.push(e.id);
  });
  if (selected && !seen[selected]) ids.push(selected);
  return ids;
}

/* 1) Format plain numbers */
assert(crFormatPlainNumber(5) === "5", "1: 5");
assert(crFormatPlainNumber(5.0) === "5", "1: 5.0");
assert(crFormatPlainNumber(333) === "333", "1: 333");
assert(crFormatPlainNumber(2.5) === "2,5", "1: 2.5");
assert(crFormatPlainNumber(12345.12) === "12345,12", "1: money");
assert(crFormatPlainNumber(-2337.5) === "-2337,5", "1: negative");
assert(crFormatPlainNumber(null) === "", "1: null");

/* 2) Author: staff sales + cover flag */
{
  const staff = { id: "tp1", _territory: true, home: "drivers", work_tab_id: "sales" };
  const cover = {
    id: "wh1",
    _territory: true,
    home: "warehouse",
    work_tab_id: "pickers",
    can_cover_sales_reps: true
  };
  const other = { id: "wh2", _territory: true, home: "warehouse", work_tab_id: "pickers" };
  assert(crEmpFitsKind(staff, "author"), "2: staff TP");
  assert(crEmpFitsKind(cover, "author"), "2: cover flag");
  assert(!crEmpFitsKind(other, "author"), "2: no cover");
}

/* 3) Cashier: primary group only */
{
  const popova = {
    id: "popova",
    _territory: true,
    home: "warehouse",
    work_tab_id: "pickers",
    work_group_id: "pickers",
    work_group_ids: ["pickers", "cashiers"]
  };
  const cashier = {
    id: "cash1",
    _territory: true,
    home: "warehouse",
    work_tab_id: "cashiers",
    work_group_id: "cashiers",
    work_group_ids: ["cashiers"]
  };
  assert(employeeMatchesWorkTab(popova, "cashiers"), "3: secondary cashiers present");
  assert(!crEmpFitsKind(popova, "cashier"), "3: Popova not cashier by primary");
  assert(crEmpFitsKind(cashier, "cashier"), "3: real cashier");
}

/* 4) Orphan selected kept */
{
  const people = [{
    id: "cash1",
    _territory: true,
    home: "warehouse",
    work_tab_id: "cashiers"
  }];
  const opts = crEmpOptionsSelected(people, "popova", "cashier");
  assert(opts.indexOf("cash1") !== -1, "4: live cashier");
  assert(opts.indexOf("popova") !== -1, "4: orphan selected");
}

console.log("wt_cr_form_lists_harness: OK");
