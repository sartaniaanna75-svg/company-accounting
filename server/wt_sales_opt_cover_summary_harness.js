/**
 * Сводка замещений ОПТ: pure-агрегация (зеркало app.html, без БД).
 * Только mode=other + actor_id; суммы с primary; exact label → metricId.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

const EXACT = "Загруженные заявки от торговых ОПТ";

function resolveSalesOptLoadedOrdersMetricFromFields(fields) {
  const hits = [];
  const seenIds = {};
  (fields || []).forEach(function (field) {
    if (!field || field.service || !field.metricId) return;
    if (String(field.label || "").trim() !== EXACT) return;
    const mid = String(field.metricId);
    if (seenIds[mid]) return;
    seenIds[mid] = true;
    hits.push(field);
  });
  if (hits.length === 1) return { ok: true, metricId: String(hits[0].metricId), reason: "" };
  if (hits.length === 0) return { ok: false, metricId: "", reason: "not_found" };
  return { ok: false, metricId: "", reason: "ambiguous" };
}

function buildSalesOptCoverSummaryRows(month, loadedMetricId, options) {
  options = options || {};
  const rowsMap = {};
  const getFact = options.getFact;
  const empCaption = options.empCaption || function (id) { return String(id || ""); };
  const map = options.actorsMap;
  if (!map || !loadedMetricId) return [];
  Object.keys(map).forEach(function (salesRepId) {
    const byDay = map[salesRepId];
    if (!byDay || typeof byDay !== "object") return;
    Object.keys(byDay).forEach(function (dayKey) {
      const rec = byDay[dayKey];
      if (!rec || typeof rec !== "object") return;
      if (String(rec.mode || "") !== "other") return;
      const actorId = String(rec.actor_id || "");
      if (!actorId) return;
      const dayNum = Number(dayKey);
      if (!Number.isFinite(dayNum) || dayNum < 1) return;
      const key = actorId + "|" + salesRepId;
      if (!rowsMap[key]) {
        rowsMap[key] = {
          actorId: actorId,
          salesRepId: salesRepId,
          actorName: empCaption(actorId),
          salesRepName: empCaption(salesRepId),
          days: {},
          sum: 0
        };
      }
      if (rowsMap[key].days[dayKey]) return;
      rowsMap[key].days[dayKey] = true;
      rowsMap[key].sum += Number(getFact(salesRepId, dayNum) || 0);
    });
  });
  return Object.keys(rowsMap).map(function (k) {
    const row = rowsMap[k];
    return {
      actorId: row.actorId,
      salesRepId: row.salesRepId,
      actorName: row.actorName,
      salesRepName: row.salesRepName,
      daysCount: Object.keys(row.days).length,
      sum: row.sum
    };
  });
}

// Exact label resolve
let r = resolveSalesOptLoadedOrdersMetricFromFields([
  { label: "Обработанные заявки от торговых ОПТ", metricId: "m_proc" },
  { label: "Загруженные заявки от торговых ОПТ", metricId: "m_loaded_live" }
]);
assert(r.ok && r.metricId === "m_loaded_live", "exact column resolve");

r = resolveSalesOptLoadedOrdersMetricFromFields([
  { label: "Загруженные заявки", metricId: "m_partial" }
]);
assert(!r.ok && r.reason === "not_found", "partial name rejected");

r = resolveSalesOptLoadedOrdersMetricFromFields([
  { label: "Загруженные заявки от торговых ОПТ", metricId: "m_a" },
  { label: "Загруженные заявки от торговых ОПТ", metricId: "m_b" }
]);
assert(!r.ok && r.reason === "ambiguous", "ambiguous rejected");

r = resolveSalesOptLoadedOrdersMetricFromFields([
  { label: "Загруженные заявки от торговых ОПТ", metricId: "m_dup" },
  { label: "Загруженные заявки от торговых ОПТ", metricId: "m_dup" }
]);
assert(r.ok && r.metricId === "m_dup", "same metricId twice → one hit");

// Facts fixture: Lera covers Natalya 14–16
const names = { e_lera: "Лера", e_nat: "Наталья", e_olga: "Ольга" };
const facts = {
  e_nat: { "14": 137107, "15": 100000, "16": 120000, "17": 50000 },
  e_olga: {}
};
const actors = {
  e_nat: {
    "14": { mode: "other", actor_id: "e_lera" },
    "15": { mode: "other", actor_id: "e_lera" },
    "16": { mode: "other", actor_id: "e_lera" },
    "17": { mode: "self" },
    "18": { mode: "unset" },
    "19": { mode: "none" },
    "20": { mode: "other", actor_id: "" }
  },
  e_olga: {
    "14": { mode: "other", actor_id: "e_lera" }
  }
};

let rows = buildSalesOptCoverSummaryRows("2026-11", "m_loaded_live", {
  actorsMap: actors,
  empCaption: function (id) { return names[id] || id; },
  getFact: function (repId, day) {
    return (facts[repId] && facts[repId][String(day)]) || 0;
  }
});

const leraNat = rows.find(function (x) { return x.actorId === "e_lera" && x.salesRepId === "e_nat"; });
assert(leraNat, "lera-nat row");
assert(leraNat.daysCount === 3, "3 days");
assert(leraNat.sum === 357107, "sum 357107");

const leraOlga = rows.find(function (x) { return x.actorId === "e_lera" && x.salesRepId === "e_olga"; });
assert(leraOlga, "lera-olga separate row");
assert(leraOlga.daysCount === 1 && leraOlga.sum === 0, "cover day without loaded → 0");

assert(!rows.some(function (x) { return x.salesRepId === "e_nat" && x.daysCount > 3; }), "self/unset/none ignored");

// Different actors by day
rows = buildSalesOptCoverSummaryRows("2026-11", "m_loaded_live", {
  actorsMap: {
    e_nat: {
      "14": { mode: "other", actor_id: "e_lera" },
      "15": { mode: "other", actor_id: "e_olga" }
    }
  },
  empCaption: function (id) { return names[id] || id; },
  getFact: function (repId, day) {
    return (facts[repId] && facts[repId][String(day)]) || 0;
  }
});
assert(rows.length === 2, "two actor rows");
assert(rows.find(function (x) { return x.actorId === "e_lera"; }).sum === 137107, "lera day sum");
assert(rows.find(function (x) { return x.actorId === "e_olga"; }).sum === 100000, "olga day sum");

// Empty month / no other
rows = buildSalesOptCoverSummaryRows("2026-11", "m_loaded_live", {
  actorsMap: { e_nat: { "1": { mode: "self" } } },
  getFact: function () { return 100; }
});
assert(rows.length === 0, "no other → empty");

// No metric id → empty
rows = buildSalesOptCoverSummaryRows("2026-11", "", { actorsMap: actors, getFact: function () { return 1; } });
assert(rows.length === 0, "no metric → empty");

console.log("wt_sales_opt_cover_summary_harness: OK");
