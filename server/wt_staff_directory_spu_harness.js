/**
 * Численность СПУ из справочника: уникальный id, аутсорсинг, fired_on на конец среды,
 * без фильтра «Участвует в СПУ». «Все» не подменяется parent.department_id.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

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

/** Mirror app.html: explicit "" = all depts; do not use || with fallback. */
function spuResolveStaffDirectoryDeptCode(sourceDeptId, fallbackDeptId) {
  if (sourceDeptId != null) return String(sourceDeptId);
  return String(fallbackDeptId == null ? "" : fallbackDeptId);
}

function spuStaffDirectoryDeptCode(it) {
  if (!it) return "";
  const sources = it.sources || [];
  for (let i = 0; i < sources.length; i++) {
    if (sources[i] && sources[i].source_type === "staff_directory") {
      return spuResolveStaffDirectoryDeptCode(sources[i].source_department_id, it.department_id);
    }
  }
  return String(it.department_id || "");
}

const DEPT_DEFAULTS = [
  { code: "shop" },
  { code: "office" },
  { code: "warehouse" },
  { code: "drivers" }
];

function mgmtStaffDirectoryFact(workspaces, deptCode, asOfIso) {
  const codes = deptCode
    ? [String(deptCode)]
    : DEPT_DEFAULTS.map(function (d) { return d.code; });
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

function mgmtStaffDirectoryPeriodIsClosed(asOfIso, today) {
  return !!(asOfIso && today && asOfIso < today);
}

function mgmtReadStaffDirectoryValue(opts) {
  const closed = mgmtStaffDirectoryPeriodIsClosed(opts.asOf, opts.today);
  const frozen = opts.frozen;
  if (closed && frozen != null && Number.isFinite(Number(frozen))) {
    return { value: Number(frozen), status: "frozen" };
  }
  if (closed) return { value: null, status: "unfrozen" };
  return { value: mgmtStaffDirectoryFact(opts.workspaces, opts.deptCode, opts.asOf), status: "auto" };
}

/** Single staff_directory source must keep frozen/unfrozen (not collapse to auto). */
function mgmtReadFromSourcesStaffDirectory(it, readFn) {
  const sources = it.sources || [];
  const use = sources.slice(0, 1);
  if (use.length === 1 && use[0] && use[0].source_type === "staff_directory") {
    return readFn(use[0], it);
  }
  return { value: null, status: "missing" };
}

assert(spuResolveStaffDirectoryDeptCode("", "office") === "", "empty All wins over parent dept");
assert(spuResolveStaffDirectoryDeptCode("shop", "office") === "shop", "explicit dept");
assert(spuResolveStaffDirectoryDeptCode(null, "office") === "office", "missing falls back");
assert(spuResolveStaffDirectoryDeptCode(undefined, "warehouse") === "warehouse", "undefined falls back");

const card = {
  indicator_id: "mind_034",
  department_id: "office",
  sources: [{ source_type: "staff_directory", source_department_id: "" }]
};
assert(spuStaffDirectoryDeptCode(card) === "", "card All with parent office");
assert(spuStaffDirectoryDeptCode({
  department_id: "office",
  sources: [{ source_type: "staff_directory", source_department_id: "warehouse" }]
}) === "warehouse", "single dept selected");

const wed = "2026-10-14";
const workspaces = {
  shop: {
    employees: [
      { id: "a", jobStatus: "working", participates_in_spu_meetings: false },
      { id: "b", jobStatus: "working", outsourcing: true, participates_in_spu_meetings: true },
      { id: "c", jobStatus: "fired", fired_on: "2026-10-14" },
      { id: "d", jobStatus: "fired", fired_on: "2026-10-15" },
      { id: "e", jobStatus: "fired", fired_on: "" },
      { id: "self_checkout", jobStatus: "working" }
    ]
  },
  office: {
    employees: [
      { id: "a", jobStatus: "working" },
      { id: "f", jobStatus: "working" },
      { id: "g", jobStatus: "working" },
      { id: "h", jobStatus: "working" },
      { id: "i", jobStatus: "working" },
      { id: "j", jobStatus: "working" },
      { id: "k", jobStatus: "working" },
      { id: "l", jobStatus: "working" },
      { id: "m", jobStatus: "working" }
    ]
  },
  warehouse: {
    employees: [{ id: "n", jobStatus: "working" }]
  },
  drivers: {
    employees: [{ id: "o", jobStatus: "working", outsourcing: true }]
  }
};

const nAll = mgmtStaffDirectoryFact(workspaces, "", wed);
// a,b,d from shop; f..m from office (a deduped); n warehouse; o drivers = 3+8+1+1 = 13
assert(nAll === 13, "all depts count got " + nAll);
const nOffice = mgmtStaffDirectoryFact(workspaces, "office", wed);
assert(nOffice === 9, "office-only would be 9 (bug symptom) got " + nOffice);
assert(nAll !== nOffice, "All must differ from single office");

assert(employeeCountsInStaffDirectoryOnDate(workspaces.shop.employees[1], wed), "outsourcing counted");
assert(!employeeCountsInStaffDirectoryOnDate(workspaces.shop.employees[2], wed), "fired on wednesday excluded");
assert(employeeCountsInStaffDirectoryOnDate(workspaces.shop.employees[3], wed), "fired after wednesday included");
assert(!employeeCountsInStaffDirectoryOnDate(workspaces.shop.employees[4], wed), "legacy fired without date excluded");
assert(!employeeCountsInStaffDirectoryOnDate(workspaces.shop.employees[5], wed), "self_checkout excluded");

const openRead = mgmtReadStaffDirectoryValue({
  workspaces: workspaces, deptCode: "", asOf: "2026-11-11", today: "2026-11-08", frozen: null
});
// asOf 11.11: d (fired_on 15.10) уже не входит → 12
assert(openRead.status === "auto" && openRead.value === 12, "open week live all got " + openRead.value);

const closedFrozen = mgmtReadStaffDirectoryValue({
  workspaces: workspaces, deptCode: "", asOf: "2026-11-04", today: "2026-11-08", frozen: 9
});
assert(closedFrozen.status === "frozen" && closedFrozen.value === 9, "closed uses freeze snapshot");

const closedUnfrozen = mgmtReadStaffDirectoryValue({
  workspaces: workspaces, deptCode: "", asOf: "2026-11-04", today: "2026-11-08", frozen: null
});
assert(closedUnfrozen.status === "unfrozen" && closedUnfrozen.value == null, "closed without snapshot");

const preserved = mgmtReadFromSourcesStaffDirectory(card, function (src, parent) {
  const dept = spuResolveStaffDirectoryDeptCode(src.source_department_id, parent.department_id);
  return mgmtReadStaffDirectoryValue({
    workspaces: workspaces, deptCode: dept, asOf: "2026-11-04", today: "2026-11-08", frozen: 9
  });
});
assert(preserved.status === "frozen" && preserved.value === 9, "fromSources keeps frozen");

const preservedLive = mgmtReadFromSourcesStaffDirectory(card, function (src, parent) {
  const dept = spuResolveStaffDirectoryDeptCode(src.source_department_id, parent.department_id);
  assert(dept === "", "fromSources All dept");
  return mgmtReadStaffDirectoryValue({
    workspaces: workspaces, deptCode: dept, asOf: "2026-11-11", today: "2026-11-08", frozen: null
  });
});
assert(preservedLive.status === "auto" && preservedLive.value === 12, "fromSources live All got " + preservedLive.value);

console.log("OK wt_staff_directory_spu_harness");
