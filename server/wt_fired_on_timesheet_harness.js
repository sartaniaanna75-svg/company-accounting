/**
 * Дата увольнения: видимость в табеле по месяцам без влияния на payroll-фильтр.
 * Зеркало employeeVisibleInTimesheetMonth / includeInMonthPayroll (без DOM).
 */
"use strict";

function isWorking(e) { return !e || e.jobStatus !== "fired"; }

function normalizeFiredOnIso(value) {
  const raw = String(value == null ? "" : value).trim();
  if (!raw) return "";
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return m[1] + "-" + m[2] + "-" + m[3];
  return "";
}

function employeeFiredOnIso(employee) {
  return normalizeFiredOnIso(employee && employee.fired_on);
}

function employeeVisibleInTimesheetMonth(employee, monthYm) {
  if (!employee || !employee.id) return false;
  const fo = employeeFiredOnIso(employee);
  if (!fo) return true;
  const ym = String(monthYm || "").slice(0, 7);
  if (!/^\d{4}-\d{2}$/.test(ym)) return true;
  return ym <= fo.slice(0, 7);
}

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

function assert(c, m) { if (!c) throw new Error(m); }

const firedOct = { id: "e1", name: "A", jobStatus: "fired", fired_on: "2026-10-15" };
const legacyFired = { id: "e2", name: "B", jobStatus: "fired", fired_on: "" };
const working = { id: "e3", name: "C", jobStatus: "working", fired_on: "" };

assert(employeeVisibleInTimesheetMonth(firedOct, "2026-09"), "sep visible");
assert(employeeVisibleInTimesheetMonth(firedOct, "2026-10"), "oct visible");
assert(!employeeVisibleInTimesheetMonth(firedOct, "2026-11"), "nov hidden");
assert(employeeVisibleInTimesheetMonth(legacyFired, "2026-11"), "legacy fired still visible");
assert(employeeVisibleInTimesheetMonth(working, "2026-11"), "working visible");

const octTs = { e1: { "10": { status: "Я" } } };
assert(includeInMonthPayroll(firedOct, octTs), "oct payroll keeps fired with marks");
assert(!includeInMonthPayroll(firedOct, {}), "nov payroll without marks excludes fired");

console.log("OK wt_fired_on_timesheet_harness");
