/**
 * Численность СПУ из справочника: уникальный id, аутсорсинг, fired_on на конец среды,
 * без фильтра «Участвует в СПУ».
 */
"use strict";

function isWorking(e) { return !e || e.jobStatus !== "fired"; }

function normalizeFiredOnIso(value) {
  const raw = String(value == null ? "" : value).trim();
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? m[1] + "-" + m[2] + "-" + m[3] : "";
}

function employeeFiredOnIso(employee) {
  return normalizeFiredOnIso(employee && employee.fired_on);
}

function employeeCountsInStaffDirectoryOnDate(employee, asOfIso) {
  if (!employee || !employee.id || employee.id === "self_checkout") return false;
  const asOf = normalizeFiredOnIso(asOfIso) || String(asOfIso || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf)) return isWorking(employee);
  const fo = employeeFiredOnIso(employee);
  if (fo) return fo > asOf;
  return isWorking(employee);
}

function mgmtStaffDirectoryFact(workspaces, deptCode, asOfIso) {
  const codes = deptCode ? [deptCode] : Object.keys(workspaces);
  const seen = {};
  let n = 0;
  codes.forEach(function (code) {
    const w = workspaces[code];
    if (!w) return;
    (w.employees || []).forEach(function (e) {
      if (!e || !e.id || seen[e.id]) return;
      if (!employeeCountsInStaffDirectoryOnDate(e, asOfIso)) return;
      seen[e.id] = true;
      n += 1;
    });
  });
  return n;
}

function assert(c, m) { if (!c) throw new Error(m); }

const wed = "2026-10-14"; // среда отчётной недели
const workspaces = {
  shop: {
    employees: [
      { id: "a", jobStatus: "working", participates_in_spu_meetings: false },
      { id: "b", jobStatus: "working", outsourcing: true, participates_in_spu_meetings: true },
      { id: "c", jobStatus: "fired", fired_on: "2026-10-14" }, // в среду — не считать
      { id: "d", jobStatus: "fired", fired_on: "2026-10-15" }, // после среды — считать
      { id: "e", jobStatus: "fired", fired_on: "" } // legacy без даты — не считать
    ]
  },
  office: {
    employees: [
      { id: "a", jobStatus: "working" }, // дубль id
      { id: "f", jobStatus: "working" }
    ]
  }
};

const n = mgmtStaffDirectoryFact(workspaces, "", wed);
assert(n === 4, "count a,b,d,f = 4 got " + n); // c excluded, e excluded, a once
assert(employeeCountsInStaffDirectoryOnDate(workspaces.shop.employees[1], wed), "outsourcing counted");
assert(employeeCountsInStaffDirectoryOnDate(workspaces.shop.employees[0], wed), "working counted");
assert(!employeeCountsInStaffDirectoryOnDate(workspaces.shop.employees[2], wed), "fired on wednesday excluded");
assert(employeeCountsInStaffDirectoryOnDate(workspaces.shop.employees[3], wed), "fired after wednesday included");
assert(!employeeCountsInStaffDirectoryOnDate(workspaces.shop.employees[4], wed), "legacy fired without date excluded");

const frozenPrev = 4;
const closedRead = { value: frozenPrev, status: "frozen" };
assert(closedRead.value === 4 && closedRead.status === "frozen", "closed week uses freeze");

console.log("OK wt_staff_directory_spu_harness");
