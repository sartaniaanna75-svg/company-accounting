/**
 * СПУ: дневные итоги WorkTime из GET-проекции spuWorktimeOverallByDay.
 * Зеркало fallback-чтения app.html. Без БД / без сырых shopMetrics.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function aggregateSum(values) {
  return (values || []).reduce(function (s, n) { return s + Number(n || 0); }, 0);
}

function spuWorktimeDayIso(d) {
  if (!d) return "";
  if (d.iso) return String(d.iso).slice(0, 10);
  return "";
}

function spuWorktimeMetricDayValue(root, metricId, iso, deptCode) {
  const mid = String(metricId || "");
  const day = String(iso || "").slice(0, 10);
  if (!root || !mid || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const dept = String(deptCode || "").trim();
  let cell = null;
  if (dept && dept !== "shop") {
    const branch = root.deptMetrics && root.deptMetrics[dept];
    cell = branch && branch[mid];
    if ((!cell || !Object.prototype.hasOwnProperty.call(cell, day))
      && root.metrics && root.metrics[mid]
      && Object.prototype.hasOwnProperty.call(root.metrics[mid], day)) {
      cell = root.metrics[mid];
    }
  } else {
    cell = root.metrics && root.metrics[mid];
  }
  if (!cell || typeof cell !== "object" || !Object.prototype.hasOwnProperty.call(cell, day)) {
    return null;
  }
  const n = Number(cell[day]);
  return Number.isFinite(n) ? n : null;
}

function spuWorktimeCollectMetricSeries(root, metricId, days, deptCode) {
  const out = { present: false, series: [], mode: "spu_projection" };
  (days || []).forEach(function (d) {
    const iso = spuWorktimeDayIso(d);
    if (!iso) return;
    const n = spuWorktimeMetricDayValue(root, metricId, iso, deptCode);
    if (n == null) return;
    out.series.push(n);
  });
  out.present = out.series.length > 0;
  return out;
}

function finishOverall(local, root, mid, days, projDept) {
  if (local && local.present) return local;
  let proj = spuWorktimeCollectMetricSeries(root, mid, days, projDept);
  if (proj.present) return proj;
  if (projDept && projDept !== "shop") {
    proj = spuWorktimeCollectMetricSeries(root, mid, days, "shop");
    if (proj.present) return proj;
  }
  return local;
}

const week = [
  { iso: "2026-11-12" },
  { iso: "2026-11-13" },
  { iso: "2026-11-14" }
];

const root = {
  metrics: {
    metric_sales: { "2026-11-12": 1000, "2026-11-13": 500 },
    store_metric_visitors: { "2026-11-12": 40 },
    warehouse_metric_inventory_result: { "2026-11-12": -15 }
  },
  deptMetrics: {
    drivers: {
      metric_opt_trips: { "2026-11-12": 7, "2026-11-13": 3 }
    }
  },
  timesheet: {
    office: {
      "2026-11-12": { ya: 2, hours: 16, late: 0, skip: 0, sick: 0, vacation: 0, weekend: 0, personal: 0 }
    }
  },
  staffDirectory: {
    office: { "2026-11-12": 5 },
    shop: { "2026-11-12": 10 }
  },
  headcountFte: {
    "": { "2026-11-18": 4.0 },
    shop: { "2026-11-18": 2.0 },
    drivers: { "2026-11-18": 1.0 },
    office: { "2026-11-18": 1.0 }
  }
};

function spuWorktimeHeadcountFte(projRoot, deptCode, weekEndIso) {
  const day = String(weekEndIso || "").slice(0, 10);
  if (!projRoot || !projRoot.headcountFte || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const key = deptCode == null || deptCode === "" ? "" : String(deptCode);
  const branch = projRoot.headcountFte[key];
  if (!branch || !Object.prototype.hasOwnProperty.call(branch, day)) return null;
  const n = Number(branch[day]);
  return Number.isFinite(n) ? n : null;
}

function headcountWithFallback(hasLocalWorkspace, deptCode, weekEnd, projRoot) {
  if (!hasLocalWorkspace) {
    const proj = spuWorktimeHeadcountFte(projRoot, deptCode, weekEnd);
    if (proj != null) return Math.round(Number(proj));
  }
  return null;
}

// A: shop sales week sum via projection
{
  const emptyLocal = { present: false, series: [] };
  const o = finishOverall(emptyLocal, root, "metric_sales", week, "shop");
  assert(o.present && aggregateSum(o.series) === 1500, "A: sales 1000+500");
}

// B: local present wins over projection
{
  const local = { present: true, series: [1, 2], mode: "cashiers_shop" };
  const o = finishOverall(local, root, "metric_sales", week, "shop");
  assert(o.mode === "cashiers_shop" && aggregateSum(o.series) === 3, "B: local wins");
}

// C: territory dept metric
{
  const o = spuWorktimeCollectMetricSeries(root, "metric_opt_trips", week, "drivers");
  assert(o.present && aggregateSum(o.series) === 10, "C: drivers trips");
}

// D: warehouse inventory builtin in metrics bucket
{
  const emptyLocal = { present: false, series: [] };
  const o = finishOverall(emptyLocal, root, "warehouse_metric_inventory_result", week, "warehouse");
  assert(o.present && aggregateSum(o.series) === -15, "D: warehouse inventory");
}

// E: no leak of raw employee cells — projection shape only aggregates
{
  assert(!root.metrics.metric_sales.e_shop, "E: no emp keys in metrics");
  assert(typeof root.deptMetrics.drivers.metric_opt_trips["2026-11-12"] === "number", "E: day number");
}

// F: headcount FTE fallback when foreign workspace missing
{
  assert(headcountWithFallback(false, "shop", "2026-11-18", root) === 2, "F: shop FTE");
  assert(headcountWithFallback(false, "drivers", "2026-11-18", root) === 1, "F: drivers FTE");
  assert(headcountWithFallback(false, "", "2026-11-18", root) === 4, "F: co FTE");
  assert(headcountWithFallback(true, "shop", "2026-11-18", root) === null, "F: local path unchanged");
}

console.log("wt_spu_worktime_overall_harness: OK");
