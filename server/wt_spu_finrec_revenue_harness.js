/**
 * СПУ: денежные итоги Финконтроля из GET-среза spuFrTerritoryRevenueByDay.
 * Зеркало overall-чтения app.html. Без БД / без raw financeRec.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function aggregateSum(values) {
  return (values || []).reduce(function (s, n) { return s + Number(n || 0); }, 0);
}

function mgmtCollectMetricOverallSeries(mid, days, root) {
  const out = { present: false, series: [], mode: "empty" };
  const field = mid === "finrec_metric_opt_total" ? "opt" : "retail_warehouse";
  if (mid !== "finrec_metric_opt_total" && mid !== "finrec_metric_retail_warehouse_total") {
    out.mode = "other";
    return out;
  }
  out.mode = "finrec_territory_revenue";
  (days || []).forEach(function (d) {
    const iso = d && d.iso ? String(d.iso).slice(0, 10) : "";
    if (!iso || !root) return;
    const cell = root[iso];
    if (!cell || typeof cell !== "object" || !Object.prototype.hasOwnProperty.call(cell, field)) return;
    const n = Number(cell[field]);
    if (!Number.isFinite(n)) return;
    out.series.push(n);
  });
  out.present = out.series.length > 0;
  return out;
}

const week = [
  { iso: "2026-11-12" },
  { iso: "2026-11-13" },
  { iso: "2026-11-14" },
  { iso: "2026-11-15" },
  { iso: "2026-11-16" },
  { iso: "2026-11-17" },
  { iso: "2026-11-18" }
];

const otherWeek = [
  { iso: "2026-11-19" },
  { iso: "2026-11-20" },
  { iso: "2026-11-21" },
  { iso: "2026-11-22" },
  { iso: "2026-11-23" },
  { iso: "2026-11-24" },
  { iso: "2026-11-25" }
];

// A: opt + retail_warehouse sums
{
  const root = {
    "2026-11-12": { opt: 100, retail_warehouse: 40 },
    "2026-11-13": { opt: 50 },
    "2026-11-14": { retail_warehouse: 10 },
    "2026-11-19": { opt: 999, retail_warehouse: 888 }
  };
  const oOpt = mgmtCollectMetricOverallSeries("finrec_metric_opt_total", week, root);
  assert(oOpt.present && aggregateSum(oOpt.series) === 150, "A: opt week 100+50");
  const oRw = mgmtCollectMetricOverallSeries("finrec_metric_retail_warehouse_total", week, root);
  assert(oRw.present && aggregateSum(oRw.series) === 50, "A: retail_wh 40+10");
  const oOther = mgmtCollectMetricOverallSeries("finrec_metric_opt_total", otherWeek, root);
  assert(oOther.present && aggregateSum(oOther.series) === 999, "A: other week not mixed");
}

// B: empty → not present
{
  const o = mgmtCollectMetricOverallSeries("finrec_metric_opt_total", week, {});
  assert(!o.present && o.series.length === 0, "B: empty root");
  const o2 = mgmtCollectMetricOverallSeries("finrec_metric_opt_total", week, null);
  assert(!o2.present, "B: null root");
}

// C: explicit zero kept when field present
{
  const root = { "2026-11-12": { opt: 0, retail_warehouse: 5 } };
  const o = mgmtCollectMetricOverallSeries("finrec_metric_opt_total", week, root);
  assert(o.present && aggregateSum(o.series) === 0 && o.series.length === 1, "C: zero opt kept");
}

console.log("wt_spu_finrec_revenue_harness: OK");
console.log("  A week sums / isolation");
console.log("  B empty → null path");
console.log("  C explicit zero");
