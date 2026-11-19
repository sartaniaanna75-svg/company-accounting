/**
 * СПУ metric_overall: участники = assignments via workMetricPeopleForTab (не hub/workAccess).
 * Зеркало mgmtMetricOverallPeople / overall series / E1-compat. Без БД.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function metricFactStorageKey(metricId, subId) {
  return subId ? String(metricId) + "::" + String(subId) : String(metricId);
}

function aggregateMetricNumbers(values, agg) {
  const nums = (values || []).map(Number).filter(function (n) { return Number.isFinite(n); });
  if (!nums.length) return 0;
  return nums.reduce(function (s, n) { return s + n; }, 0);
}

function getDeptMetricStored(factsRoot, deptCode, employeeId, day, metricId, month, subId) {
  const key = metricFactStorageKey(metricId, subId);
  const monthStore = factsRoot[deptCode] && factsRoot[deptCode][month];
  const rec = monthStore && monthStore[employeeId] && monthStore[employeeId][String(day)];
  if (!rec) return 0;
  return rec[key] != null ? Number(rec[key] || 0) : 0;
}

function mgmtDeptMetricFactIsPresent(factsRoot, deptCode, employeeId, day, metricId, month, subId) {
  const key = metricFactStorageKey(metricId, subId);
  const monthStore = factsRoot[deptCode] && factsRoot[deptCode][month];
  const rec = monthStore && monthStore[employeeId] && monthStore[employeeId][String(day)];
  if (!rec || typeof rec !== "object") return false;
  if (rec[key] != null && rec[key] !== "") return true;
  return false;
}

function metricDepartmentId(item) {
  return (item && item.department_id) || "";
}

function metricWorkTabIds(item) {
  if (!item) return [];
  const ids = [];
  (item.assignments || []).forEach(function (row) {
    if (!row || !row.work_tab_id) return;
    if (ids.indexOf(row.work_tab_id) === -1) ids.push(row.work_tab_id);
  });
  if (ids.length) return ids;
  return (item.work_tab_ids || []).slice();
}

/** Mirror: people from assignments (specific / role / extra); workAccess ignored. */
function peopleAssignedToMetricOnTab(item, deptCode, tabId, rolePool) {
  const list = [];
  const seen = {};
  function add(id) {
    if (!id || seen[id]) return;
    seen[id] = true;
    list.push({ id: id });
  }
  (item.assignments || []).forEach(function (row) {
    if (!row || row.department_id !== deptCode || row.work_tab_id !== tabId) return;
    if (row.assign_mode === "specific") {
      (row.employee_ids || []).forEach(add);
    } else {
      (rolePool[deptCode + "|" + tabId + "|" + (row.position_id || "*")] || []).forEach(add);
    }
    (row.extra_employee_ids || []).forEach(add);
  });
  return list;
}

function workMetricPeopleForTab(deptCode, metricId, tabId, catalogById, rolePool) {
  const item = catalogById[metricId];
  if (!item) return [];
  if (item.assignments && item.assignments.length) {
    return peopleAssignedToMetricOnTab(item, deptCode, tabId, rolePool);
  }
  return [];
}

function mgmtMetricOverallPeople(catalogItem, catalogById, rolePool) {
  if (!catalogItem) return [];
  const mid = catalogItem.id;
  if (metricDepartmentId(catalogItem) === "shop") {
    return (rolePool["shop|" + mid] || []).map(function (id) { return { id: id }; });
  }
  const seen = {};
  const list = [];
  function addPeople(arr) {
    (arr || []).forEach(function (e) {
      if (!e || !e.id || seen[e.id]) return;
      seen[e.id] = true;
      list.push(e);
    });
  }
  const asgPairs = {};
  (catalogItem.assignments || []).forEach(function (row) {
    if (!row || !row.department_id || !row.work_tab_id) return;
    if (row.department_id === "shop") return;
    const key = row.department_id + "|" + row.work_tab_id;
    if (asgPairs[key]) return;
    asgPairs[key] = true;
    addPeople(workMetricPeopleForTab(row.department_id, mid, row.work_tab_id, catalogById, rolePool));
  });
  if (list.length) return list;
  const host = metricDepartmentId(catalogItem) || "";
  const tabs = metricWorkTabIds(catalogItem);
  if (!host) return list;
  tabs.forEach(function (tab) {
    addPeople(workMetricPeopleForTab(host, mid, tab, catalogById, rolePool));
  });
  return list;
}

function mgmtCollectMetricOverallSeries(opts) {
  const mid = opts.metricId;
  const item = opts.catalogById[mid];
  const out = { present: false, series: [], mode: "empty", value: null };
  if (!item) { out.mode = "no_catalog"; return out; }
  if (item.fill_type === "vehicle") { out.mode = "vehicle"; return out; }
  const people = mgmtMetricOverallPeople(item, opts.catalogById, opts.rolePool);
  if (item.fill_type === "auto_calc") {
    out.mode = "auto_calc";
    return out; // covered elsewhere; pct stays null
  }
  const isShop = metricDepartmentId(item) === "shop";
  (opts.days || []).forEach(function (d) {
    people.forEach(function (e) {
      if (isShop) {
        const shopRec = opts.shopFacts[d.month] && opts.shopFacts[d.month][String(d.day)] && opts.shopFacts[d.month][String(d.day)][e.id];
        if (!shopRec || shopRec[mid] == null) return;
        out.series.push(Number(shopRec[mid] || 0));
        return;
      }
      const factDept = opts.homeDept[e.id] || item.department_id || "office";
      if (!mgmtDeptMetricFactIsPresent(opts.factsRoot, factDept, e.id, d.day, mid, d.month, "")) return;
      out.series.push(Number(getDeptMetricStored(opts.factsRoot, factDept, e.id, d.day, mid, d.month, "") || 0));
    });
  });
  out.present = out.series.length > 0;
  out.mode = "day_facts";
  return out;
}

function overallSum(opts) {
  const o = mgmtCollectMetricOverallSeries(opts);
  if (!o.present) return null;
  return aggregateMetricNumbers(o.series, "sum");
}

const MONTH = "2026-11";
const day11 = [{ day: 11, month: MONTH }];
const day1112 = [{ day: 11, month: MONTH }, { day: 12, month: MONTH }];

const catalogById = {
  m_sales_opt: {
    id: "m_sales_opt",
    department_id: "drivers",
    territory_category: "sales_opt",
    assignments: [{
      department_id: "drivers",
      work_tab_id: "sales",
      assign_mode: "specific",
      employee_ids: ["e1", "e2", "e3", "e4", "e5"],
      extra_employee_ids: []
    }]
  },
  m_pick: {
    id: "m_pick",
    department_id: "warehouse",
    territory_category: "warehouse",
    assignments: [{
      department_id: "warehouse",
      work_tab_id: "loaders",
      assign_mode: "role",
      position_id: "pos_loader",
      employee_ids: [],
      extra_employee_ids: ["e_extra"]
    }]
  },
  m_office: {
    id: "m_office",
    department_id: "office",
    territory_category: "office",
    assignments: [{
      department_id: "office",
      work_tab_id: "main",
      assign_mode: "specific",
      employee_ids: ["e_off"],
      extra_employee_ids: []
    }]
  },
  m_driver_km: {
    id: "m_driver_km",
    department_id: "drivers",
    territory_category: "drivers",
    assignments: [
      {
        department_id: "drivers",
        work_tab_id: "drivers",
        assign_mode: "specific",
        employee_ids: ["e_drv"],
        extra_employee_ids: []
      },
      {
        department_id: "drivers",
        work_tab_id: "sales",
        assign_mode: "specific",
        employee_ids: ["e_drv", "e_rep"],
        extra_employee_ids: []
      }
    ]
  },
  m_shop_checks: {
    id: "m_shop_checks",
    department_id: "shop"
  }
};

const rolePool = {
  "warehouse|loaders|pos_loader": ["e_loader", "e_loader2"],
  "shop|m_shop_checks": ["e_cash"]
};

const workAccessOnly = ["e_access_only"]; // has access, NOT assigned

const factsRoot = {
  drivers: {
    "2026-11": {
      e1: { "11": { m_sales_opt: 10 } },
      e2: { "11": { m_sales_opt: 20 } },
      e3: { "11": { m_sales_opt: 30 } },
      e4: { "11": { m_sales_opt: 40 } },
      e5: { "11": { m_sales_opt: 50 } }, // no workAccess in fixture narrative
      e_access_only: { "11": { m_sales_opt: 999 } },
      e_drv: { "11": { m_driver_km: 5 }, "12": { m_driver_km: 7 } },
      e_rep: { "11": { m_driver_km: 1 } }
    }
  },
  warehouse: {
    "2026-11": {
      e_loader: { "11": { m_pick: 3 } },
      e_loader2: { "11": { m_pick: 4 } },
      e_extra: { "11": { m_pick: 8 } },
      e_stranger: { "11": { m_pick: 777 } }
    }
  },
  office: {
    "2026-11": {
      e_off: { "11": { m_office: 2 }, "12": { m_office: 3 } }
    }
  }
};

const shopFacts = {
  "2026-11": {
    "11": { e_cash: { m_shop_checks: 18 }, e_other: { m_shop_checks: 1 } }
  }
};

const homeDept = {
  e1: "drivers", e2: "drivers", e3: "drivers", e4: "drivers", e5: "drivers",
  e_access_only: "drivers", e_drv: "drivers", e_rep: "drivers",
  e_loader: "warehouse", e_loader2: "warehouse", e_extra: "warehouse", e_stranger: "warehouse",
  e_off: "office"
};

const base = {
  catalogById: catalogById,
  rolePool: rolePool,
  factsRoot: factsRoot,
  shopFacts: shopFacts,
  homeDept: homeDept
};

// 1) Пять назначенных; e5 без workAccess — все пять с фактами в overall
{
  const people = mgmtMetricOverallPeople(catalogById.m_sales_opt, catalogById, rolePool);
  assert(people.length === 5, "1: five assigned");
  assert(people.every(function (e) { return ["e1", "e2", "e3", "e4", "e5"].indexOf(e.id) !== -1; }), "1: ids");
  const v = overallSum(Object.assign({}, base, { metricId: "m_sales_opt", days: day11 }));
  assert(v === 10 + 20 + 30 + 40 + 50, "1: sum five facts " + v);
}

// 2) workAccess-only не назначен — не учитывается
{
  const people = mgmtMetricOverallPeople(catalogById.m_sales_opt, catalogById, rolePool);
  assert(!people.some(function (e) { return e.id === "e_access_only"; }), "2: access-only excluded");
  const v = overallSum(Object.assign({}, base, { metricId: "m_sales_opt", days: day11 }));
  assert(v !== 10 + 20 + 30 + 40 + 50 + 999, "2: access-only fact not in sum");
  void workAccessOnly;
}

// 3) Назначение через должность (role)
{
  const people = mgmtMetricOverallPeople(catalogById.m_pick, catalogById, rolePool);
  assert(people.some(function (e) { return e.id === "e_loader"; }), "3: role e_loader");
  assert(people.some(function (e) { return e.id === "e_loader2"; }), "3: role e_loader2");
}

// 4) extra_employee_ids
{
  const people = mgmtMetricOverallPeople(catalogById.m_pick, catalogById, rolePool);
  assert(people.some(function (e) { return e.id === "e_extra"; }), "4: extra included");
  const v = overallSum(Object.assign({}, base, { metricId: "m_pick", days: day11 }));
  assert(v === 3 + 4 + 8, "4: role+extra sum");
  assert(v !== 3 + 4 + 8 + 777, "4: stranger out");
}

// 5) Один сотрудник на нескольких вкладках — один раз
{
  const people = mgmtMetricOverallPeople(catalogById.m_driver_km, catalogById, rolePool);
  const drvHits = people.filter(function (e) { return e.id === "e_drv"; }).length;
  assert(drvHits === 1, "5: e_drv once");
  assert(people.length === 2, "5: e_drv + e_rep");
  const v = overallSum(Object.assign({}, base, { metricId: "m_driver_km", days: day1112 }));
  assert(v === 5 + 7 + 1, "5: no double-count e_drv");
}

// 6) ОПТ / склад / офис / водители — одно правило (assignments)
{
  assert(overallSum(Object.assign({}, base, { metricId: "m_sales_opt", days: day11 })) === 150, "6: OPT");
  assert(overallSum(Object.assign({}, base, { metricId: "m_pick", days: day11 })) === 15, "6: warehouse");
  assert(overallSum(Object.assign({}, base, { metricId: "m_office", days: day1112 })) === 5, "6: office");
  assert(overallSum(Object.assign({}, base, { metricId: "m_driver_km", days: day11 })) === 5 + 1, "6: drivers");
}

// 7) Магазин: shop branch; stranger e_other не участник
{
  const v = overallSum(Object.assign({}, base, { metricId: "m_shop_checks", days: day11 }));
  assert(v === 18, "7: shop participant only");
}

// 8) Legacy group path people ≠ overall when access-only in facts (documented)
{
  const overallPeople = mgmtMetricOverallPeople(catalogById.m_sales_opt, catalogById, rolePool).map(function (e) { return e.id; });
  assert(overallPeople.indexOf("e_access_only") === -1, "8: overall not hub/workAccess");
  assert(overallPeople.indexOf("e5") !== -1, "8: assigned without access kept");
}

console.log("wt_spu_metric_overall_harness: OK");
