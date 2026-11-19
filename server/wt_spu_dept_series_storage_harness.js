/**
 * E1: collectDeptMetricPeriodSeries — trips только для territory_category=drivers;
 * sales_opt и прочие day-fact категории читают facts по metric_id.
 * Зеркало логики app.html, без БД. Покрывает 9 проверок PLAN «СПУ чтение фактов».
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function metricFactStorageKey(metricId, subId) {
  return subId ? String(metricId) + "::" + String(subId) : String(metricId);
}

function dayMatchesFactDays(day, month, days) {
  const dNum = Number(day);
  return (days || []).some(function (d) {
    return Number(d.day) === dNum && String(d.month || "") === String(month || "");
  });
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
  if (rec[key + "Mark"] === "done" || rec[key + "Mark"] === "fail") return true;
  return false;
}

function aggregateMetricNumbers(values, agg) {
  const nums = (values || []).map(Number).filter(function (n) { return Number.isFinite(n); });
  if (agg === "count") return nums.length;
  if (!nums.length) return 0;
  if (agg === "avg") return nums.reduce(function (s, n) { return s + n; }, 0) / nums.length;
  if (agg === "last") return nums[nums.length - 1];
  return nums.reduce(function (s, n) { return s + n; }, 0);
}

/**
 * Зеркало E1-guard: trips-first только при territoryCat === "drivers".
 */
function collectDeptMetricPeriodSeries(opts) {
  const mid = String(opts.metricId || "");
  const series = [];
  if (!mid || !opts.deptCode) return series;
  const catalogItem = opts.catalogById && opts.catalogById[mid];
  if (catalogItem && catalogItem.fill_type === "vehicle") return series;
  const allow = opts.employeeIds && opts.employeeIds.length ? opts.employeeIds : null;
  const empOk = function (id) { return !allow || allow.indexOf(id) !== -1; };
  const territoryCat = (catalogItem && catalogItem.territory_category) || "";
  const subId = opts.subId || "";
  if (opts.deptCode === "drivers" && !subId && territoryCat === "drivers") {
    const months = {};
    (opts.days || []).forEach(function (d) { months[d.month || opts.defaultMonth] = true; });
    let fromTrips = false;
    Object.keys(months).forEach(function (m) {
      (opts.entriesByMonth[m] || []).forEach(function (entry) {
        if (!entry || !empOk(entry.employee_id)) return;
        if (!dayMatchesFactDays(entry.day, m, opts.days)) return;
        if (entry.values && entry.values[mid] != null && entry.values[mid] !== "") {
          series.push(Number(entry.values[mid] || 0));
          fromTrips = true;
        }
      });
    });
    if (fromTrips) return series;
  }
  (opts.days || []).forEach(function (d) {
    const facts = opts.factsRoot[opts.deptCode] && opts.factsRoot[opts.deptCode][d.month];
    Object.keys(facts || {}).forEach(function (empId) {
      if (!empOk(empId)) return;
      const rec = facts[empId] && facts[empId][String(d.day)];
      if (!rec) return;
      const key = metricFactStorageKey(mid, subId);
      if (rec[key] == null && rec[key + "Mark"] == null) return;
      series.push(Number(getDeptMetricStored(opts.factsRoot, opts.deptCode, empId, d.day, mid, d.month, subId) || 0));
    });
  });
  return series;
}

function mgmtCollectAssignedWorktimeMetricSeries(opts) {
  const mid = String(opts.metricId || "");
  const tab = String(opts.workTabId || "");
  const host = String(opts.hostDept || "");
  const series = [];
  if (!mid || !host || !tab) return { present: false, series: series };
  const people = opts.peopleForTab(host, mid, tab) || [];
  (opts.days || []).forEach(function (d) {
    people.forEach(function (e) {
      if (!e || !e.id) return;
      const factDept = opts.factDeptFor(e.id, host);
      if (!mgmtDeptMetricFactIsPresent(opts.factsRoot, factDept, e.id, d.day, mid, d.month, opts.subId || "")) {
        return;
      }
      series.push(getDeptMetricStored(opts.factsRoot, factDept, e.id, d.day, mid, d.month, opts.subId || ""));
    });
  });
  return { present: series.length > 0, series: series };
}

function collectShopMetricPeriodSeries(shopFacts, metricId, days) {
  const mid = String(metricId || "");
  const series = [];
  (days || []).forEach(function (d) {
    const dayMap = shopFacts[d.month] && shopFacts[d.month][String(d.day)];
    if (!dayMap) return;
    Object.keys(dayMap).forEach(function (empId) {
      const rec = dayMap[empId];
      if (!rec || rec[mid] == null) return;
      series.push(Number(rec[mid] || 0));
    });
  });
  return series;
}

function pad(n) { return (n < 10 ? "0" : "") + n; }
function isoFromDate(d) {
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}
function parseIso(iso) {
  const p = String(iso || "").split("-").map(Number);
  return new Date(p[0], (p[1] || 1) - 1, p[2] || 1);
}
function reportingWeekWithStartDay(startDay, iso) {
  const d = parseIso(iso);
  const start = Number(startDay);
  const offset = (d.getDay() - start + 7) % 7;
  const from = new Date(d.getFullYear(), d.getMonth(), d.getDate() - offset);
  const to = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 6);
  return { from: isoFromDate(from), to: isoFromDate(to) };
}
function daysBetweenToFactDays(fromIso, toIso) {
  const out = [];
  const cur = parseIso(fromIso);
  const end = parseIso(toIso);
  while (cur <= end) {
    out.push({ day: cur.getDate(), month: cur.getFullYear() + "-" + pad(cur.getMonth() + 1) });
    cur.setDate(cur.getDate() + 1);
  }
  return out;
}

// --- fixtures ---
const MONTH = "2026-11";
const days1113 = [
  { day: 11, month: MONTH },
  { day: 12, month: MONTH },
  { day: 13, month: MONTH }
];

const catalogById = {
  m_sales_opt: { id: "m_sales_opt", territory_category: "sales_opt" },
  m_driver_km: { id: "m_driver_km", territory_category: "drivers" },
  m_pick: { id: "m_pick", territory_category: "warehouse" },
  m_shop_checks: { id: "m_shop_checks", department_id: "shop" }
};

const factsRoot = {
  drivers: {
    "2026-11": {
      e_nat: {
        "11": { m_sales_opt: 527470, m_other: 999 },
        "12": { m_sales_opt: 534984 },
        "13": { m_sales_opt: 548190 },
        "14": { m_sales_opt: 0 }
      },
      e_drv: {
        "11": { m_sales_opt: 100000, m_driver_km: 50 }
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

const entriesByMonth = {
  "2026-11": [
    { employee_id: "e_drv", day: 11, values: { m_sales_opt: 840772, m_driver_km: 12 } },
    { employee_id: "e_drv", day: 12, values: { m_driver_km: 8 } }
  ]
};

const shopFacts = {
  "2026-11": {
    "11": { e_cashier: { m_shop_checks: 18 } },
    "12": { e_cashier: { m_shop_checks: 3 } }
  }
};

const peopleByTab = {
  "drivers|m_sales_opt|sales": [{ id: "e_nat" }],
  "warehouse|m_sort|sorters": [{ id: "e_sorter" }]
};

function peopleForTab(host, mid, tab) {
  return peopleByTab[host + "|" + mid + "|" + tab] || [];
}
function factDeptFor(empId, host) {
  if (empId === "e_sorter" || empId === "e_loader") return "warehouse";
  return host;
}

const baseCollect = {
  factsRoot: factsRoot,
  entriesByMonth: entriesByMonth,
  catalogById: catalogById,
  defaultMonth: MONTH
};

// 1) Метрика по ID: в сумму только её ключ
{
  const series = collectDeptMetricPeriodSeries(Object.assign({}, baseCollect, {
    deptCode: "drivers",
    metricId: "m_sales_opt",
    days: [{ day: 11, month: MONTH }],
    employeeIds: ["e_nat"]
  }));
  assert(series.length === 1 && series[0] === 527470, "1: only m_sales_opt key");
  assert(aggregateMetricNumbers(series, "sum") !== 527470 + 999, "1: m_other excluded");
}

// 2) group_total + sales: day-facts; entries игнорируются
{
  const collected = mgmtCollectAssignedWorktimeMetricSeries({
    factsRoot: factsRoot,
    peopleForTab: peopleForTab,
    factDeptFor: factDeptFor,
    days: days1113,
    hostDept: "drivers",
    metricId: "m_sales_opt",
    workTabId: "sales"
  });
  const v = aggregateMetricNumbers(collected.series, "sum");
  assert(v === 527470 + 534984 + 548190, "2: group_total sales sum " + v);
  assert(v !== 840772, "2: entries ignored");
  assert(v !== 527470 + 534984 + 548190 + 100000, "2: only assigned e_nat");
}

// 3) department_total + sales_opt: day-facts даже при мусоре в trips
{
  const series = collectDeptMetricPeriodSeries(Object.assign({}, baseCollect, {
    deptCode: "drivers",
    metricId: "m_sales_opt",
    days: days1113
  }));
  const v = aggregateMetricNumbers(series, "sum");
  // e_nat 11+12+13 + e_drv day11 fact
  assert(v === 527470 + 534984 + 548190 + 100000, "3: sales_opt day-facts " + v);
  assert(series.indexOf(840772) === -1, "3: trip junk excluded");
}

// 4) department_total + drivers: trips сохраняются
{
  const series = collectDeptMetricPeriodSeries(Object.assign({}, baseCollect, {
    deptCode: "drivers",
    metricId: "m_driver_km",
    days: [
      { day: 11, month: MONTH },
      { day: 12, month: MONTH }
    ]
  }));
  assert(series.length === 2, "4: two trip values");
  assert(aggregateMetricNumbers(series, "sum") === 12 + 8, "4: trips 12+8");
  // day-facts m_driver_km=50 на e_drv не должны подмешиваться при fromTrips
  assert(series.indexOf(50) === -1, "4: day-fact skipped when trips present");
}

// 5) legacy fact (тот же коллектор) для sales_opt: day-facts, не trips
{
  const series = collectDeptMetricPeriodSeries(Object.assign({}, baseCollect, {
    deptCode: "drivers",
    metricId: "m_sales_opt",
    days: [{ day: 11, month: MONTH }],
    employeeIds: ["e_drv"]
  }));
  assert(series.length === 1 && series[0] === 100000, "5: legacy fact = day-fact");
  assert(series[0] !== 840772, "5: not trip");
}

// 6) Склад group / shop series
{
  const wh = mgmtCollectAssignedWorktimeMetricSeries({
    factsRoot: factsRoot,
    peopleForTab: peopleForTab,
    factDeptFor: factDeptFor,
    days: [{ day: 11, month: MONTH }, { day: 12, month: MONTH }],
    hostDept: "warehouse",
    metricId: "m_sort",
    workTabId: "sorters"
  });
  assert(aggregateMetricNumbers(wh.series, "sum") === 12, "6: warehouse group 5+7");

  const whDept = collectDeptMetricPeriodSeries(Object.assign({}, baseCollect, {
    deptCode: "warehouse",
    metricId: "m_pick",
    days: [{ day: 11, month: MONTH }]
  }));
  assert(aggregateMetricNumbers(whDept, "sum") === 10, "6: warehouse department_total");

  const shopSeries = collectShopMetricPeriodSeries(shopFacts, "m_shop_checks", [
    { day: 11, month: MONTH },
    { day: 12, month: MONTH }
  ]);
  assert(aggregateMetricNumbers(shopSeries, "sum") === 21, "6: shop series 18+3");
}

// 7) Два источника → корректные входы в sum (без смены формулы)
{
  const srcA = aggregateMetricNumbers(
    collectDeptMetricPeriodSeries(Object.assign({}, baseCollect, {
      deptCode: "drivers",
      metricId: "m_sales_opt",
      days: [{ day: 11, month: MONTH }],
      employeeIds: ["e_nat"]
    })),
    "sum"
  );
  const srcB = aggregateMetricNumbers(
    collectDeptMetricPeriodSeries(Object.assign({}, baseCollect, {
      deptCode: "warehouse",
      metricId: "m_pick",
      days: [{ day: 11, month: MONTH }]
    })),
    "sum"
  );
  assert(srcA === 527470, "7: source A = sales_opt");
  assert(srcB === 10, "7: source B = pick");
  assert(srcA + srcB === 527480, "7: formula sum inputs");
  assert(srcA !== 840772, "7: A not trips");
}

// 8) Week-start 4 и 3; неделя через границу месяца
{
  const thuWed = reportingWeekWithStartDay(4, "2026-11-12");
  assert(thuWed.from === "2026-11-12" && thuWed.to === "2026-11-18", "8: Thu–Wed");
  const wedTue = reportingWeekWithStartDay(3, "2026-11-12");
  assert(wedTue.from === "2026-11-11" && wedTue.to === "2026-11-17", "8: Wed–Tue");
  const cross = reportingWeekWithStartDay(4, "2026-12-03"); // Thu Dec 3
  assert(cross.from === "2026-12-03" && cross.to === "2026-12-09", "8: Dec week");
  const crossWed = reportingWeekWithStartDay(3, "2026-12-01"); // Tue Dec 1 → Wed Nov 25 – Tue Dec 1
  assert(crossWed.from === "2026-11-25" && crossWed.to === "2026-12-01", "8: month boundary Wed–Tue");
  const crossDays = daysBetweenToFactDays(crossWed.from, crossWed.to);
  assert(crossDays.some(function (d) { return d.month === "2026-11" && d.day === 25; }), "8: Nov days");
  assert(crossDays.some(function (d) { return d.month === "2026-12" && d.day === 1; }), "8: Dec days");
}

// 9) Нет double-count; пустой ключ не в сумме; явный 0 в сумме
{
  const series = collectDeptMetricPeriodSeries(Object.assign({}, baseCollect, {
    deptCode: "drivers",
    metricId: "m_sales_opt",
    days: [
      { day: 11, month: MONTH },
      { day: 14, month: MONTH }
    ],
    employeeIds: ["e_nat"]
  }));
  assert(series.length === 2, "9: two present cells");
  assert(aggregateMetricNumbers(series, "sum") === 527470 + 0, "9: explicit 0 kept");
  const missing = collectDeptMetricPeriodSeries(Object.assign({}, baseCollect, {
    deptCode: "drivers",
    metricId: "m_missing",
    days: days1113
  }));
  assert(missing.length === 0, "9: empty key not in series");
  // trips+facts не должны оба попасть для sales_opt
  const full = collectDeptMetricPeriodSeries(Object.assign({}, baseCollect, {
    deptCode: "drivers",
    metricId: "m_sales_opt",
    days: [{ day: 11, month: MONTH }]
  }));
  const tripHits = full.filter(function (n) { return n === 840772; }).length;
  assert(tripHits === 0, "9: no trip double-count");
}

console.log("wt_spu_dept_series_storage_harness: OK");
