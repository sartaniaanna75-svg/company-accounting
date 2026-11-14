/**
 * СПУ worktime group_total: только назначенные сотрудники вкладки + day-facts (home).
 * Без entries, без смешения групп. Empty ≠ 0. Зеркало логики app.html, без БД.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function metricFactStorageKey(metricId, subId) {
  return subId ? String(metricId) + "::" + String(subId) : String(metricId);
}

function mgmtDeptMetricFactIsPresent(factsRoot, deptCode, employeeId, day, metricId, month, submetricId) {
  const key = metricFactStorageKey(metricId, submetricId);
  const monthStore = factsRoot[deptCode] && factsRoot[deptCode][month];
  const rec = monthStore && monthStore[employeeId] && monthStore[employeeId][String(day)];
  if (!rec || typeof rec !== "object") return false;
  if (rec[key] != null && rec[key] !== "") return true;
  if (rec[key + "Mark"] === "done" || rec[key + "Mark"] === "fail") return true;
  return false;
}

function getDeptMetricStored(factsRoot, deptCode, employeeId, day, metricId, month, submetricId) {
  const key = metricFactStorageKey(metricId, submetricId);
  const monthStore = factsRoot[deptCode] && factsRoot[deptCode][month];
  const rec = monthStore && monthStore[employeeId] && monthStore[employeeId][String(day)];
  if (!rec) return 0;
  return rec[key] != null ? Number(rec[key] || 0) : 0;
}

function aggregateMetricNumbers(values, agg) {
  const nums = (values || []).map(Number).filter(function (n) { return Number.isFinite(n); });
  if (agg === "count") return nums.length;
  if (!nums.length) return 0;
  if (agg === "avg") return nums.reduce(function (s, n) { return s + n; }, 0) / nums.length;
  if (agg === "last") return nums[nums.length - 1];
  return nums.reduce(function (s, n) { return s + n; }, 0);
}

function mgmtCollectAssignedWorktimeMetricSeries(opts) {
  const mid = String(opts.metricId || "");
  const tab = String(opts.workTabId || "");
  const host = String(opts.hostDept || "");
  const series = [];
  if (!mid || !host || !tab) return { present: false, series: series };
  const people = opts.peopleForTab(host, mid, tab) || [];
  (opts.days || []).forEach(function (d) {
    const month = d.month;
    const dayNum = d.day;
    people.forEach(function (e) {
      if (!e || !e.id) return;
      const factDept = opts.factDeptFor(e.id, host);
      if (!mgmtDeptMetricFactIsPresent(opts.factsRoot, factDept, e.id, dayNum, mid, month, opts.subId || "")) {
        return;
      }
      series.push(getDeptMetricStored(opts.factsRoot, factDept, e.id, dayNum, mid, month, opts.subId || ""));
    });
  });
  return { present: series.length > 0, series: series };
}

function mgmtMetricFactGroupTotal(opts) {
  const collected = mgmtCollectAssignedWorktimeMetricSeries(opts);
  if (!collected.present) return null;
  return aggregateMetricNumbers(collected.series, opts.agg || "sum");
}

// --- fixtures ---
const MONTH = "2026-11";
const days = [
  { day: 11, month: MONTH },
  { day: 12, month: MONTH },
  { day: 13, month: MONTH }
];
const factsRoot = {
  drivers: {
    "2026-11": {
      e_nat: {
        "11": { m_loaded: 527470 },
        "12": { m_loaded: 534984 },
        "13": { m_loaded: 548190 }
      },
      e_drv: {
        "11": { m_loaded: 100000 },
        "12": { m_trips: 900 }
      }
    }
  },
  warehouse: {
    "2026-11": {
      e_loader: { "11": { m_pick: 10 } },
      e_sorter: { "11": { m_sort: 5 }, "12": { m_sort: 7 } }
    }
  }
};
// orphan entries layer (must NOT be used)
const entries = [{ employee_id: "e_drv", day: 11, values: { m_loaded: 840772 } }];

const peopleByTab = {
  "drivers|m_loaded|sales": [{ id: "e_nat" }],
  "drivers|m_loaded|drivers": [{ id: "e_drv" }],
  "warehouse|m_sort|sorters": [{ id: "e_sorter" }],
  "warehouse|m_pick|loaders": [{ id: "e_loader" }]
};

function peopleForTab(host, mid, tab) {
  return peopleByTab[host + "|" + mid + "|" + tab] || [];
}
function factDeptFor(empId, host) {
  if (empId === "e_sorter" || empId === "e_loader") return "warehouse";
  return host;
}

const baseOpts = {
  factsRoot: factsRoot,
  peopleForTab: peopleForTab,
  factDeptFor: factDeptFor,
  days: days,
  agg: "sum"
};

// 1) Sales metric: only Natalya, ignore driver facts + entries
let v = mgmtMetricFactGroupTotal(Object.assign({}, baseOpts, {
  hostDept: "drivers",
  metricId: "m_loaded",
  workTabId: "sales"
}));
assert(v === 527470 + 534984 + 548190, "sales sum got " + v);
assert(v !== 840772, "must not use entries");
assert(v !== 527470 + 534984 + 548190 + 100000, "must not include driver emp");

// 2) Drivers tab same metric id: only driver assigned
v = mgmtMetricFactGroupTotal(Object.assign({}, baseOpts, {
  hostDept: "drivers",
  metricId: "m_loaded",
  workTabId: "drivers",
  days: [{ day: 11, month: MONTH }]
}));
assert(v === 100000, "drivers tab only e_drv day11");

// 3) Warehouse sorters vs loaders — no mix
v = mgmtMetricFactGroupTotal(Object.assign({}, baseOpts, {
  hostDept: "warehouse",
  metricId: "m_sort",
  workTabId: "sorters",
  days: [{ day: 11, month: MONTH }, { day: 12, month: MONTH }]
}));
assert(v === 12, "sorters 5+7");

v = mgmtMetricFactGroupTotal(Object.assign({}, baseOpts, {
  hostDept: "warehouse",
  metricId: "m_pick",
  workTabId: "loaders",
  days: [{ day: 11, month: MONTH }]
}));
assert(v === 10, "loaders only");

// 4) Empty metric → null (not 0), even if entries/other metric exist
v = mgmtMetricFactGroupTotal(Object.assign({}, baseOpts, {
  hostDept: "drivers",
  metricId: "m_missing",
  workTabId: "sales"
}));
assert(v === null, "empty → null");

// 5) Explicit zero is a real fact
factsRoot.drivers["2026-11"].e_nat["14"] = { m_loaded: 0 };
v = mgmtMetricFactGroupTotal(Object.assign({}, baseOpts, {
  hostDept: "drivers",
  metricId: "m_loaded",
  workTabId: "sales",
  days: [{ day: 14, month: MONTH }]
}));
assert(v === 0, "explicit 0 kept");

// 6) Partial days only
v = mgmtMetricFactGroupTotal(Object.assign({}, baseOpts, {
  hostDept: "drivers",
  metricId: "m_loaded",
  workTabId: "sales",
  days: [
    { day: 11, month: MONTH },
    { day: 15, month: MONTH }
  ]
}));
assert(v === 527470, "only present day");

// Prove entries unused even if huge
assert(entries[0].values.m_loaded === 840772, "fixture entries exist");

console.log("wt_spu_metric_group_fact_harness: OK");
