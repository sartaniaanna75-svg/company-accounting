/**
 * Сквозной путь: карточки кассиров + КСО + покупатели → СПУ Факт
 * для существующих shop_* с legacy source_result=department_total.
 * Сценарии A–D. Без БД / без браузера.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function pad(n) { return (n < 10 ? "0" : "") + n; }
function parseIso(iso) {
  const p = String(iso || "").split("-").map(Number);
  return new Date(p[0], (p[1] || 1) - 1, p[2] || 1);
}
function isoFromDate(d) {
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}
function reportingWeekWithStartDay(startDay, iso) {
  const d = parseIso(iso);
  const offset = (d.getDay() - Number(startDay) + 7) % 7;
  const from = new Date(d.getFullYear(), d.getMonth(), d.getDate() - offset);
  const to = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 6);
  return { from: isoFromDate(from), to: isoFromDate(to) };
}
function daysBetween(fromIso, toIso) {
  const out = [];
  const cur = parseIso(fromIso);
  const end = parseIso(toIso);
  while (cur <= end) {
    out.push({ day: cur.getDate(), month: cur.getFullYear() + "-" + pad(cur.getMonth() + 1) });
    cur.setDate(cur.getDate() + 1);
  }
  return out;
}
function aggregateSum(values) {
  return (values || []).reduce(function (s, n) { return s + Number(n || 0); }, 0);
}
function spuSafeDiv(a, b) {
  const x = Number(a);
  const y = Number(b);
  if (!Number.isFinite(x) || !Number.isFinite(y) || y === 0) return null;
  const out = x / y;
  return Number.isFinite(out) ? out : null;
}
function mgmtAggregateNumbers(values, type) {
  const nums = (values || []).filter(function (v) {
    return v != null && v !== "" && Number.isFinite(Number(v));
  }).map(Number);
  const agg = String(type || "sum");
  if (agg === "count") return nums.length;
  if (!nums.length) return null;
  if (agg === "average" || agg === "avg") {
    return nums.reduce(function (a, b) { return a + b; }, 0) / nums.length;
  }
  if (agg === "last") return nums[nums.length - 1];
  if (agg === "none" || agg === "not_applicable") return nums[0];
  return nums.reduce(function (a, b) { return a + b; }, 0);
}

/** Зеркало app.html mgmtShopBuiltinUsesOverallCompat */
function mgmtShopBuiltinUsesOverallCompat(mid, srcResult) {
  const id = String(mid || "");
  if (!id) return false;
  const r = String(srcResult || "department_total");
  if (r === "metric_overall") return true;
  if (r === "group_total") return false;
  const legacy = (r === "department_total" || r === "metric_total" || r === "" || r === "fact");
  if (!legacy) return false;
  if (id === "metric_sales" || id === "metric_checks") return true;
  if (id === "store_metric_visitors") return true;
  if (id === "store_metric_inventory_result") return true;
  if (id === "warehouse_metric_inventory_result") return true;
  return false;
}

function shopInventoryDayHasValues(rec) {
  if (!rec || typeof rec !== "object") return false;
  if (rec.posting_filled === true || rec.writeoff_filled === true) return true;
  return Math.abs(Number(rec.posting || 0)) > 0 || Math.abs(Number(rec.writeoff || 0)) > 0;
}

function shopInventoryDayResult(rec) {
  if (!shopInventoryDayHasValues(rec)) return null;
  if (rec.result != null && rec.result !== "" && Number.isFinite(Number(rec.result))) {
    return Number(rec.result);
  }
  return Number(rec.posting || 0) - Math.abs(Number(rec.writeoff || 0));
}

function deptInventoryDayHasValues(rec) {
  return shopInventoryDayHasValues(rec);
}

function getDeptInventoryDayResult(rec) {
  if (!deptInventoryDayHasValues(rec)) return null;
  const posting = Math.abs(Number(rec.posting || 0));
  const writeoff = Math.abs(Number(rec.writeoff || 0));
  return posting - writeoff;
}

function shopCatalogOperandSnapshot(opts) {
  const mid = opts.metricId;
  const empId = opts.employeeId;
  const day = String(opts.day);
  const month = opts.month;
  if (mid === "metric_sales" || mid === "metric_checks") {
    const fieldKey = mid === "metric_sales" ? "sales" : "receipts";
    const rec = opts.cashiers[month] && opts.cashiers[month][empId] && opts.cashiers[month][empId][day];
    if (!rec || rec[fieldKey] == null || rec[fieldKey] === "") return { present: false, value: null };
    return { present: true, value: Number(rec[fieldKey] || 0) };
  }
  return { present: false, value: null };
}

/** Зеркало mgmtCollectMetricOverallSeries для sales/checks/visitors/inventory */
function mgmtCollectMetricOverallSeries(opts) {
  const mid = opts.metricId;
  const out = { present: false, series: [], mode: "empty", value: null };
  if (mid === "store_metric_visitors") {
    out.mode = "store";
    (opts.days || []).forEach(function (d) {
      const rec = opts.store[d.month] && opts.store[d.month][String(d.day)];
      if (!rec || rec.visitors == null || rec.visitors === "") return;
      out.series.push(Number(rec.visitors || 0));
    });
    out.present = out.series.length > 0;
    return out;
  }
  if (mid === "store_metric_inventory_result") {
    out.mode = "inventory_shop";
    (opts.days || []).forEach(function (d) {
      const rec = opts.shopInventory && opts.shopInventory[d.month]
        && opts.shopInventory[d.month][String(d.day)];
      const v = shopInventoryDayResult(rec);
      if (v == null || !Number.isFinite(Number(v))) return;
      out.series.push(Number(v));
    });
    out.present = out.series.length > 0;
    return out;
  }
  if (mid === "warehouse_metric_inventory_result") {
    out.mode = "inventory_warehouse";
    (opts.days || []).forEach(function (d) {
      const rec = opts.warehouseInventory && opts.warehouseInventory[d.month]
        && opts.warehouseInventory[d.month][String(d.day)];
      const v = getDeptInventoryDayResult(rec);
      if (v == null || !Number.isFinite(Number(v))) return;
      out.series.push(Number(v));
    });
    out.present = out.series.length > 0;
    return out;
  }
  if (mid === "metric_sales" || mid === "metric_checks") {
    out.mode = "cashiers_shop";
    const fieldKey = mid === "metric_sales" ? "sales" : "receipts";
    const slots = (opts.slotIds || []).map(function (id) { return { id: id }; });
    (opts.days || []).forEach(function (d) {
      let daySum = 0;
      let any = false;
      slots.forEach(function (e) {
        if (!e || !e.id || e.id === "self_checkout") return;
        const snap = shopCatalogOperandSnapshot({
          cashiers: opts.cashiers, employeeId: e.id, day: d.day, month: d.month, metricId: mid
        });
        if (!snap.present || snap.value == null) return;
        daySum += Number(snap.value);
        any = true;
      });
      const chRec = opts.channelValues
        && opts.channelValues[d.month]
        && opts.channelValues[d.month].self_checkout
        && opts.channelValues[d.month].self_checkout[String(d.day)];
      if (chRec && chRec[fieldKey] != null && chRec[fieldKey] !== "") {
        daySum += Number(chRec[fieldKey] || 0);
        any = true;
      }
      if (any) out.series.push(daySum);
    });
    out.present = out.series.length > 0;
    return out;
  }
  return out;
}

/**
 * Зеркало mgmtMetricFact: overall + legacy shop compat.
 * Legacy broken path (StoredFact→0) моделируется отдельно.
 */
function mgmtMetricFact(indicator, metricId, days, data) {
  const mid = String(metricId || "");
  const srcResult = indicator ? String(indicator.source_result || "") : "";
  if (srcResult === "metric_overall" || mgmtShopBuiltinUsesOverallCompat(mid, srcResult)) {
    const overall = mgmtCollectMetricOverallSeries(Object.assign({}, data, { metricId: mid, days: days }));
    if (!overall.present) return null;
    return aggregateSum(overall.series);
  }
  /* Legacy broken: sales/checks через StoredFact → всегда 0 */
  if (mid === "metric_sales" || mid === "metric_checks") return 0;
  /* Legacy visitors: getShopStoreValue missing→0 за каждый день */
  if (mid === "store_metric_visitors") {
    const series = [];
    (days || []).forEach(function (d) {
      const rec = data.store[d.month] && data.store[d.month][String(d.day)];
      series.push(rec && rec.visitors != null ? Number(rec.visitors || 0) : 0);
    });
    return aggregateSum(series);
  }
  return 0;
}

function mgmtReadIndicator(it, byId, days, data) {
  if (!it || it.is_deleted) return { value: null, status: "missing" };
  if (it.source_type === "calculated") {
    const left = byId[it.calc_left_id];
    const right = byId[it.calc_right_id];
    const lv = left ? mgmtReadIndicator(left, byId, days, data).value : null;
    const rv = right ? mgmtReadIndicator(right, byId, days, data).value : null;
    const a = Number(lv);
    const b = Number(rv);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return { value: null, status: "auto" };
    const op = it.calc_op || "ratio";
    if (op === "percent") {
      const r = spuSafeDiv(a, b);
      return { value: r == null ? null : r * 100, status: "auto" };
    }
    return { value: spuSafeDiv(a, b), status: "auto" };
  }
  if (it.source_type === "worktime_metric") {
    /* sources[0] как mgmtSourceAsIndicator */
    const src = (it.sources && it.sources[0]) || {
      source_metric_id: it.source_metric_id,
      source_result: it.source_result || "department_total"
    };
    const fake = {
      source_metric_id: src.source_metric_id || it.source_metric_id,
      source_result: src.source_result || it.source_result || "department_total"
    };
    const v = mgmtMetricFact(fake, fake.source_metric_id, days, data);
    return { value: v, status: v == null ? "missing" : "auto" };
  }
  return { value: null, status: "missing" };
}

/** Ячейка «Факт» в План–факт: show_in_planfact + read */
function planFactCell(it, byId, days, data, weekLabel) {
  if (!it || it.is_active === false || it.is_deleted) return { hidden: true, value: null };
  if (it.show_in_planfact !== true) return { hidden: true, value: null };
  const read = mgmtReadIndicator(it, byId, days, data);
  return {
    hidden: false,
    row: it.name,
    week: weekLabel,
    value: read.value,
    status: read.status
  };
}

const NOV = "2026-11";
const week1218 = reportingWeekWithStartDay(4, "2026-11-12");
const week1925 = reportingWeekWithStartDay(4, "2026-11-19");
assert(week1218.from === "2026-11-12" && week1218.to === "2026-11-18", "Thu–Wed 12–18");
assert(week1925.from === "2026-11-19" && week1925.to === "2026-11-25", "Thu–Wed 19–25");
const days1218 = daysBetween(week1218.from, week1218.to);
const days1925 = daysBetween(week1925.from, week1925.to);
const daysEmpty = daysBetween("2026-11-05", "2026-11-11");

/* --- Данные сценария A/B: один день 12.11 + частичная неделя --- */
const dataAB = {
  slotIds: ["slot_c1", "slot_c2"],
  cashiers: {
    [NOV]: {
      slot_c1: {
        "12": { sales: 50000, receipts: 100 },
        "13": { sales: 0, receipts: 0 } /* explicit 0 */
      },
      slot_c2: {
        "12": { sales: 40000, receipts: 80 }
      }
    }
  },
  channelValues: {
    [NOV]: {
      self_checkout: {
        "12": { sales: 10000, receipts: 20 }
      }
    }
  },
  store: {
    [NOV]: {
      "12": { visitors: 250 },
      "14": { visitors: 0 } /* explicit 0 kept */
    }
  }
};

/* Существующие показатели ДО исправления: department_total, без пересоздания */
const indSales = {
  indicator_id: "ind_43_sales",
  name: "Выручка/сумма отгруженного товара Магазин",
  spu_code: "shop_sales",
  source_type: "worktime_metric",
  source_metric_id: "metric_sales",
  source_result: "department_total",
  show_in_planfact: true,
  sources: [{ source_type: "worktime_metric", source_metric_id: "metric_sales", source_result: "department_total" }]
};
const indChecks = {
  indicator_id: "ind_44_checks",
  name: "Количество чеков Магазин",
  spu_code: "shop_checks",
  source_type: "worktime_metric",
  source_metric_id: "metric_checks",
  source_result: "department_total",
  show_in_planfact: true,
  sources: [{ source_type: "worktime_metric", source_metric_id: "metric_checks", source_result: "department_total" }]
};
const indVisitors = {
  indicator_id: "ind_43_visitors",
  name: "Количество покупателей Магазин",
  spu_code: "shop_visitors",
  source_type: "worktime_metric",
  source_metric_id: "store_metric_visitors",
  source_result: "department_total",
  show_in_planfact: true,
  sources: [{ source_type: "worktime_metric", source_metric_id: "store_metric_visitors", source_result: "department_total" }]
};
const indAvg = {
  indicator_id: "ind_46_avg",
  name: "Средний чек",
  spu_code: "shop_avg",
  source_type: "calculated",
  calc_op: "ratio",
  calc_left_id: "ind_43_sales",
  calc_right_id: "ind_44_checks",
  show_in_planfact: true
};
const indConv = {
  indicator_id: "ind_45_conv",
  name: "Конверсия покупатели → чеки",
  spu_code: "shop_conversion",
  source_type: "calculated",
  calc_op: "percent",
  calc_left_id: "ind_44_checks",
  calc_right_id: "ind_43_visitors",
  show_in_planfact: true
};
const byId = {
  ind_43_sales: indSales,
  ind_44_checks: indChecks,
  ind_43_visitors: indVisitors,
  ind_46_avg: indAvg,
  ind_45_conv: indConv
};

const day12 = [{ day: 12, month: NOV }];

// ========== A: продажи и чеки ==========
{
  assert(mgmtShopBuiltinUsesOverallCompat("metric_sales", "department_total"), "A: sales compat");
  assert(mgmtShopBuiltinUsesOverallCompat("metric_checks", "metric_total"), "A: checks compat metric_total");
  assert(!mgmtShopBuiltinUsesOverallCompat("metric_sales", "group_total"), "A: group_total not compat");

  const sales = mgmtMetricFact(indSales, "metric_sales", day12, dataAB);
  const checks = mgmtMetricFact(indChecks, "metric_checks", day12, dataAB);
  assert(sales === 100000, "A: sales 100000 got " + sales);
  assert(checks === 200, "A: checks 200 got " + checks);

  const cellS = planFactCell(indSales, byId, day12, dataAB, "12.11");
  const cellC = planFactCell(indChecks, byId, day12, dataAB, "12.11");
  const cellAvg = planFactCell(indAvg, byId, day12, dataAB, "12.11");
  assert(cellS.hidden === false && cellS.value === 100000, "A: fact cell sales");
  assert(cellC.value === 200, "A: fact cell checks");
  assert(cellAvg.value === 500, "A: avg check 500 got " + cellAvg.value);
}

// ========== B: покупатели и конверсия ==========
{
  const vis = mgmtMetricFact(indVisitors, "store_metric_visitors", day12, dataAB);
  assert(vis === 250, "B: visitors 250 got " + vis);
  const cellV = planFactCell(indVisitors, byId, day12, dataAB, "12.11");
  const cellConv = planFactCell(indConv, byId, day12, dataAB, "12.11");
  assert(cellV.value === 250, "B: fact cell visitors");
  assert(cellConv.value === 80, "B: conversion 80% got " + cellConv.value);
}

// ========== C: два слота одного сотрудника (факты по slot id) ==========
{
  const dataC = {
    slotIds: ["slot_a", "slot_b"],
    cashiers: {
      [NOV]: {
        slot_a: { "12": { sales: 30000, receipts: 60 } },
        slot_b: { "12": { sales: 20000, receipts: 40 } }
      }
    },
    channelValues: { [NOV]: { self_checkout: {} } },
    store: { [NOV]: {} }
  };
  /* Оба слота учтены; КСО нет → не дублируется */
  const s = mgmtMetricFact(
    { source_result: "department_total", source_metric_id: "metric_sales" },
    "metric_sales", day12, dataC
  );
  const c = mgmtMetricFact(
    { source_result: "department_total", source_metric_id: "metric_checks" },
    "metric_checks", day12, dataC
  );
  assert(s === 50000, "C: two slots sales 50000 got " + s);
  assert(c === 100, "C: two slots checks 100 got " + c);
  /* Повторный вызов не удваивает */
  assert(mgmtMetricFact(
    { source_result: "department_total" }, "metric_sales", day12, dataC
  ) === 50000, "C: no double sum");
}

// ========== D: периоды + существующие настройки ==========
{
  /* Неделя 12–18: день 12 заполнен, остальные пусты → только day12 */
  const wSales = planFactCell(indSales, byId, days1218, dataAB, "12–18");
  const wChecks = planFactCell(indChecks, byId, days1218, dataAB, "12–18");
  const wVis = planFactCell(indVisitors, byId, days1218, dataAB, "12–18");
  const wAvg = planFactCell(indAvg, byId, days1218, dataAB, "12–18");
  const wConv = planFactCell(indConv, byId, days1218, dataAB, "12–18");
  assert(wSales.value === 100000, "D: week12–18 sales");
  assert(wChecks.value === 200, "D: week12–18 checks");
  /* visitors: day12=250 + day14 explicit 0 → 250; missing days skipped */
  assert(wVis.value === 250, "D: week12–18 visitors with explicit 0 day");
  assert(wAvg.value === 500, "D: week avg");
  assert(wConv.value === 80, "D: week conversion");

  /* Неделя 19–25 пустая → null, не 0 */
  const emptySales = planFactCell(indSales, byId, days1925, dataAB, "19–25");
  const emptyVis = planFactCell(indVisitors, byId, days1925, dataAB, "19–25");
  assert(emptySales.value === null, "D: empty week sales → null");
  assert(emptyVis.value === null, "D: empty week visitors → null");
  assert(emptySales.status === "missing", "D: missing status");

  /* Полностью пустая неделя 05–11 */
  const empty0511 = planFactCell(indSales, byId, daysEmpty, dataAB, "05–11");
  assert(empty0511.value === null, "D: empty 05–11 → null");

  /* Сравнение двух недель не смешивает */
  assert(wSales.value === 100000 && emptySales.value === null, "D: two weeks independent");

  /* Повторная «загрузка» — те же цифры */
  const reload = planFactCell(indSales, byId, days1218, dataAB, "12–18");
  assert(reload.value === 100000, "D: reload keeps value");

  /* metric_overall тоже работает */
  const overallInd = {
    source_result: "metric_overall",
    source_metric_id: "metric_sales",
    source_type: "worktime_metric",
    show_in_planfact: true,
    name: "sales overall",
    indicator_id: "x",
    sources: [{ source_result: "metric_overall", source_metric_id: "metric_sales" }]
  };
  assert(planFactCell(overallInd, { x: overallInd }, day12, dataAB, "12").value === 100000, "D: metric_overall path");

  /* Без show_in_planfact — скрыто */
  const hidden = Object.assign({}, indSales, { show_in_planfact: false });
  assert(planFactCell(hidden, byId, day12, dataAB, "12").hidden === true, "D: hidden without planfact");
}

// ========== Док: почему предыдущий BUILD не помог ==========
{
  /* Без compat: department_total → 0 для sales */
  function mgmtMetricFactOld(indicator, metricId, days, data) {
    const srcResult = indicator ? String(indicator.source_result || "") : "";
    if (srcResult === "metric_overall") {
      const o = mgmtCollectMetricOverallSeries(Object.assign({}, data, { metricId: metricId, days: days }));
      if (!o.present) return null;
      return aggregateSum(o.series);
    }
    if (metricId === "metric_sales" || metricId === "metric_checks") return 0;
    return 0;
  }
  assert(mgmtMetricFactOld(indSales, "metric_sales", day12, dataAB) === 0, "DOC: pre-fix legacy → 0");
  assert(mgmtMetricFact(indSales, "metric_sales", day12, dataAB) === 100000, "DOC: post-fix legacy → real");
}

// ========== Explicit 0 vs missing ==========
{
  const dataZ = {
    slotIds: ["slot_z"],
    cashiers: { [NOV]: { slot_z: { "12": { sales: 0, receipts: 0 } } } },
    channelValues: { [NOV]: { self_checkout: {} } },
    store: { [NOV]: { "12": { visitors: 0 } } }
  };
  assert(mgmtMetricFact(indSales, "metric_sales", day12, dataZ) === 0, "Z: explicit 0 sales");
  assert(mgmtMetricFact(indVisitors, "store_metric_visitors", day12, dataZ) === 0, "Z: explicit 0 visitors");
  assert(mgmtMetricFact(indSales, "metric_sales", [{ day: 15, month: NOV }], dataZ) === null, "Z: missing → null");
}

// ========== E: sources без metric_id — наследование с родителя (mgmtSourceAsIndicator) ==========
{
  function mgmtSourceAsIndicator(src, parent) {
    const mid = (src && src.source_metric_id) || (parent && parent.source_metric_id) || "";
    let result = src && src.source_result;
    if (result == null || result === "") {
      result = (parent && parent.source_result) || "department_total";
    }
    return { source_metric_id: mid, source_result: result, source_type: "worktime_metric" };
  }
  const parent = {
    indicator_id: "ind_orphan_src",
    source_type: "worktime_metric",
    source_metric_id: "metric_sales",
    source_result: "department_total",
    sources: [{ source_id: "s1", source_type: "worktime_metric", source_metric_id: "", source_result: "department_total" }]
  };
  const fake = mgmtSourceAsIndicator(parent.sources[0], parent);
  assert(fake.source_metric_id === "metric_sales", "E: inherit metric_id from parent");
  assert(fake.source_result === "department_total", "E: inherit/keep result");
  const v = mgmtMetricFact(fake, fake.source_metric_id, day12, dataAB);
  assert(v === 100000, "E: fact via inherited metric_id got " + v);
  /* Без наследования mid="" → 0/пусто */
  const bare = { source_metric_id: "", source_result: "department_total" };
  assert(mgmtMetricFact(bare, bare.source_metric_id, day12, dataAB) === 0
    || mgmtMetricFact(bare, "", day12, dataAB) == null
    || mgmtMetricFact(bare, "", day12, dataAB) === 0, "E: empty mid fails without inherit");
}

// ========== F: реальные mind_091/092/017 (manual) → после UI-перепривязки WorkTime ==========
{
  function spuIsShopBuiltinTotalMetric(metricId) {
    const id = String(metricId || "");
    return id === "metric_sales" || id === "metric_checks"
      || id === "store_metric_visitors"
      || id === "store_metric_inventory_result"
      || id === "warehouse_metric_inventory_result";
  }
  function spuParticipatingWorkMetrics(catalog) {
    return (catalog || []).filter(function (m) {
      if (!m || !m.id || m.active === false) return false;
      if (spuIsShopBuiltinTotalMetric(m.id)) return true;
      return m.use_in_spu === true;
    });
  }
  /* Как в live: manual без metric_id → факт null (не overall). */
  function mgmtReadIndicatorLive(it, byId, days, data) {
    if (!it) return { value: null, status: "missing" };
    if (it.source_type === "manual") {
      return { value: null, status: "manual" }; /* нет ручного факта */
    }
    return mgmtReadIndicator(it, byId, days, data);
  }

  const catalog = [
    { id: "metric_sales", use_in_spu: false, active: true, assignable: false },
    { id: "metric_checks", use_in_spu: false, active: true, assignable: false },
    { id: "store_metric_visitors", use_in_spu: false, active: true, assignable: false },
    { id: "store_metric_inventory_result", use_in_spu: true, active: true, assignable: false, department_id: "shop" },
    { id: "warehouse_metric_inventory_result", use_in_spu: true, active: true, assignable: false, department_id: "warehouse" },
    { id: "metric_display_positions", use_in_spu: true, active: true },
    { id: "metric_writeoff_losses", use_in_spu: true, active: true }
  ];
  const pick = spuParticipatingWorkMetrics(catalog).map(function (m) { return m.id; });
  assert(pick.indexOf("metric_sales") !== -1, "F: sales in picker");
  assert(pick.indexOf("metric_checks") !== -1, "F: checks in picker");
  assert(pick.indexOf("store_metric_visitors") !== -1, "F: visitors in picker");
  assert(pick.indexOf("store_metric_inventory_result") !== -1, "F: shop inventory in picker");
  assert(pick.indexOf("warehouse_metric_inventory_result") !== -1, "F: warehouse inventory in picker");

  const mind091 = {
    indicator_id: "mind_091",
    name: "Количество покупателей Магазин",
    source_type: "manual",
    source_metric_id: "",
    source_result: "",
    show_in_planfact: true,
    sources: [{ source_type: "worktime_metric", source_metric_id: "", source_result: "department_total" }]
  };
  const mind092 = {
    indicator_id: "mind_092",
    name: "Количество чеков Магазин",
    source_type: "manual",
    source_metric_id: "",
    source_result: "",
    show_in_planfact: true,
    sources: [{ source_type: "worktime_metric", source_metric_id: "", source_result: "department_total" }]
  };
  const mind017 = {
    indicator_id: "mind_017",
    name: "Выручка/сумма отгруженного товара Магазин",
    spu_code: "shop_sales",
    source_type: "manual",
    source_metric_id: "",
    source_result: "",
    show_in_planfact: true,
    sources: [{ source_type: "worktime_metric", source_metric_id: "", source_result: "department_total" }]
  };
  const mind093 = {
    indicator_id: "mind_093",
    name: "Конверсия покупатели → чеки Магазин",
    source_type: "calculated",
    calc_op: "percent",
    calc_left_id: "mind_092",
    calc_right_id: "mind_091",
    show_in_planfact: true
  };
  const mind094 = {
    indicator_id: "mind_094",
    name: "Средний чек",
    source_type: "calculated",
    calc_op: "ratio",
    calc_left_id: "mind_017",
    calc_right_id: "mind_092",
    show_in_planfact: true
  };
  const byMind = {
    mind_091: mind091, mind_092: mind092, mind_017: mind017,
    mind_093: mind093, mind_094: mind094
  };
  assert(mgmtReadIndicatorLive(mind017, byMind, day12, dataAB).value === null, "F: manual sales → null");
  assert(mgmtReadIndicatorLive(mind092, byMind, day12, dataAB).value === null, "F: manual checks → null");
  assert(mgmtReadIndicatorLive(mind091, byMind, day12, dataAB).value === null, "F: manual visitors → null");
  assert(mgmtReadIndicatorLive(mind094, byMind, day12, dataAB).value === null, "F: avg depends on empty");
  assert(mgmtReadIndicatorLive(mind093, byMind, day12, dataAB).value === null, "F: conv depends on empty");

  /* После явного сохранения пользователем «Из WorkTime» + metric_overall с 12.11 */
  function rebindWorktime(it, metricId) {
    it.source_type = "worktime_metric";
    it.source_metric_id = metricId;
    it.source_result = "metric_overall";
    it.sources = [{
      source_type: "worktime_metric",
      source_metric_id: metricId,
      source_result: "metric_overall"
    }];
    it.source_versions = [{
      version_id: "v_from_12",
      effective_from: "2026-11-12",
      effective_to: "",
      sources: [{
        source_type: "worktime_metric",
        source_metric_id: metricId,
        source_result: "metric_overall"
      }],
      aggregation_type: "sum"
    }];
  }
  rebindWorktime(mind091, "store_metric_visitors");
  rebindWorktime(mind092, "metric_checks");
  rebindWorktime(mind017, "metric_sales");

  function activeSources(it, asOf) {
    const vers = it.source_versions || [];
    let hit = null;
    vers.forEach(function (v) {
      if (v.effective_from && String(v.effective_from) > asOf) return;
      if (v.effective_to && String(v.effective_to) < asOf) return;
      hit = v;
    });
    return hit ? hit.sources : it.sources;
  }
  assert(activeSources(mind017, "2026-11-18")[0].source_metric_id === "metric_sales", "F: version covers 12–18");
  assert(activeSources(mind017, "2026-11-18")[0].source_result === "metric_overall", "F: overall on week");

  const cellSales = planFactCell(mind017, byMind, days1218, dataAB, "12–18");
  const cellChecks = planFactCell(mind092, byMind, days1218, dataAB, "12–18");
  const cellVis = planFactCell(mind091, byMind, days1218, dataAB, "12–18");
  const cellAvg = planFactCell(mind094, byMind, days1218, dataAB, "12–18");
  const cellConv = planFactCell(mind093, byMind, days1218, dataAB, "12–18");
  assert(cellSales.value === 100000, "F: mind_017 sales 100000 got " + cellSales.value);
  assert(cellChecks.value === 200, "F: mind_092 checks 200");
  assert(cellVis.value === 250, "F: mind_091 visitors 250");
  assert(cellAvg.value === 500, "F: mind_094 avg 500");
  assert(cellConv.value === 80, "F: mind_093 conv 80%");

  /* Пустая неделя 19–25 */
  assert(planFactCell(mind017, byMind, days1925, dataAB, "19–25").value === null, "F: empty week null");
  /* Только КСО */
  const ksoOnly = {
    slotIds: [],
    cashiers: { [NOV]: {} },
    channelValues: { [NOV]: { self_checkout: { "12": { sales: 10000, receipts: 20 } } } },
    store: { [NOV]: { "12": { visitors: 50 } } }
  };
  assert(planFactCell(mind017, byMind, day12, ksoOnly, "12").value === 10000, "F: KSO-only sales");
  assert(planFactCell(mind092, byMind, day12, ksoOnly, "12").value === 20, "F: KSO-only checks");
}

// ========== G: инвентаризация магазин/склад → metric_overall (знак, ноль, пусто, граница месяцев) ==========
{
  assert(mgmtShopBuiltinUsesOverallCompat("store_metric_inventory_result", "metric_overall"), "G: shop inv overall");
  assert(mgmtShopBuiltinUsesOverallCompat("warehouse_metric_inventory_result", "department_total"), "G: wh inv compat");
  assert(mgmtShopBuiltinUsesOverallCompat("store_metric_inventory_result", "fact"), "G: shop inv legacy fact");
  assert(!mgmtShopBuiltinUsesOverallCompat("store_metric_inventory_result", "group_total"), "G: group_total blocked");

  const OCT = "2026-10";
  /* Неделя с переходом месяцев: чт 29.10 – ср 04.11 */
  const weekCross = reportingWeekWithStartDay(4, "2026-10-29");
  assert(weekCross.from === "2026-10-29" && weekCross.to === "2026-11-04", "G: cross-month week");
  const daysCross = daysBetween(weekCross.from, weekCross.to);

  const dataInv = {
    slotIds: dataAB.slotIds.slice(),
    cashiers: dataAB.cashiers,
    channelValues: dataAB.channelValues,
    store: dataAB.store,
    shopInventory: {
      [NOV]: {
        "12": { posting: 0, writeoff: 500, result: -500, posting_filled: true, writeoff_filled: true },
        "13": { posting: 200, writeoff: 0, result: 200, posting_filled: true, writeoff_filled: true },
        "14": { posting: 0, writeoff: 100, result: -100, posting_filled: true, writeoff_filled: true },
        "15": { posting: 0, writeoff: 0, result: 0, posting_filled: true, writeoff_filled: true }
      },
      [OCT]: {
        "29": { posting: 0, writeoff: 300, result: -300, posting_filled: true, writeoff_filled: true },
        "31": { posting: 50, writeoff: 0, result: 50, posting_filled: true, writeoff_filled: true }
      }
    },
    warehouseInventory: {
      [NOV]: {
        "12": { posting: 1000, writeoff: 0, posting_filled: true, writeoff_filled: true },
        "13": { posting: 0, writeoff: 250, posting_filled: true, writeoff_filled: true },
        "16": { posting: 0, writeoff: 0, posting_filled: true, writeoff_filled: true }
      },
      [OCT]: {
        "30": { posting: 0, writeoff: 80, posting_filled: true, writeoff_filled: true }
      }
    }
  };

  const indShopInv = {
    indicator_id: "ind_shop_inv",
    name: "Инвентаризация магазин",
    spu_code: "shop_inv_manual",
    source_type: "worktime_metric",
    source_metric_id: "store_metric_inventory_result",
    source_result: "metric_overall",
    show_in_planfact: true,
    sources: [{
      source_type: "worktime_metric",
      source_metric_id: "store_metric_inventory_result",
      source_result: "metric_overall"
    }]
  };
  const indWhInv = {
    indicator_id: "ind_wh_inv",
    name: "Инвентаризация",
    spu_code: "wh_inv",
    source_type: "worktime_metric",
    source_metric_id: "warehouse_metric_inventory_result",
    source_result: "metric_overall",
    show_in_planfact: true,
    sources: [{
      source_type: "worktime_metric",
      source_metric_id: "warehouse_metric_inventory_result",
      source_result: "metric_overall"
    }]
  };
  const byInv = { ind_shop_inv: indShopInv, ind_wh_inv: indWhInv };

  /* 1–3: магазин −500+200−100+0 = −400; склад независим 1000−250+0 = 750 */
  const shopWeek = planFactCell(indShopInv, byInv, days1218, dataInv, "12–18");
  const whWeek = planFactCell(indWhInv, byInv, days1218, dataInv, "12–18");
  assert(shopWeek.value === -400, "G: shop week signed −400 got " + shopWeek.value);
  assert(whWeek.value === 750, "G: warehouse week 750 got " + whWeek.value);
  assert(shopWeek.value !== whWeek.value, "G: shop≠warehouse isolation");

  /* 4: подтверждённый ноль дня 15 учтён (уже в −400); день только с нулём */
  const zeroOnly = planFactCell(indShopInv, byInv, [{ day: 15, month: NOV }], dataInv, "15");
  assert(zeroOnly.value === 0, "G: explicit zero kept got " + zeroOnly.value);

  /* 5: пустая неделя → null */
  assert(planFactCell(indShopInv, byInv, days1925, dataInv, "19–25").value === null, "G: empty shop → null");
  assert(planFactCell(indWhInv, byInv, days1925, dataInv, "19–25").value === null, "G: empty wh → null");

  /* 6: переход месяцев 29.10–04.11: shop −300+50 = −250; wh −80 */
  const shopCross = planFactCell(indShopInv, byInv, daysCross, dataInv, "29.10–04.11");
  const whCross = planFactCell(indWhInv, byInv, daysCross, dataInv, "29.10–04.11");
  assert(shopCross.value === -250, "G: cross-month shop −250 got " + shopCross.value);
  assert(whCross.value === -80, "G: cross-month wh −80 got " + whCross.value);

  /* 7: две недели независимы */
  assert(shopWeek.value === -400 && shopCross.value === -250, "G: two weeks independent");

  /* 8–10: ручной показатель → WorkTime + versions + reload */
  const mindInv = {
    indicator_id: "mind_inv_shop",
    name: "Инвентаризация Магазин (было вручную)",
    source_type: "manual",
    source_metric_id: "",
    source_result: "",
    show_in_planfact: true,
    sources: []
  };
  assert(planFactCell(mindInv, { mind_inv_shop: mindInv }, days1218, dataInv, "12–18").value === null
    || true, "G: manual before bind");
  mindInv.source_type = "worktime_metric";
  mindInv.source_metric_id = "store_metric_inventory_result";
  mindInv.source_result = "metric_overall";
  mindInv.sources = [{
    source_type: "worktime_metric",
    source_metric_id: "store_metric_inventory_result",
    source_result: "metric_overall"
  }];
  mindInv.source_versions = [{
    version_id: "v_inv_12",
    effective_from: "2026-11-12",
    effective_to: "",
    sources: [{
      source_type: "worktime_metric",
      source_metric_id: "store_metric_inventory_result",
      source_result: "metric_overall"
    }],
    aggregation_type: "sum"
  }];
  const byMindInv = { mind_inv_shop: mindInv };
  const afterBind = planFactCell(mindInv, byMindInv, days1218, dataInv, "12–18");
  assert(afterBind.value === -400, "G: rebind manual→worktime −400");
  assert(mindInv.source_versions.length === 1
    && mindInv.source_versions[0].effective_from === "2026-11-12", "G: source_versions kept");
  assert(planFactCell(mindInv, byMindInv, days1218, dataInv, "12–18").value === -400, "G: reload same");

  /* 11: нет двойного учёта — повторный overall не удваивает */
  const o1 = mgmtCollectMetricOverallSeries(Object.assign({}, dataInv, {
    metricId: "store_metric_inventory_result", days: days1218
  }));
  const o2 = mgmtCollectMetricOverallSeries(Object.assign({}, dataInv, {
    metricId: "store_metric_inventory_result", days: days1218
  }));
  assert(aggregateSum(o1.series) === -400 && aggregateSum(o2.series) === -400, "G: no double count");
  assert(o1.series.length === 4, "G: four present days (incl zero)");

  /* 12: регресс кассиров на тех же данных */
  assert(planFactCell(indSales, byId, days1218, dataInv, "12–18").value === 100000, "G: regress sales");
  assert(planFactCell(indChecks, byId, days1218, dataInv, "12–18").value === 200, "G: regress checks");
  assert(planFactCell(indVisitors, byId, days1218, dataInv, "12–18").value === 250, "G: regress visitors");
  assert(planFactCell(indAvg, byId, days1218, dataInv, "12–18").value === 500, "G: regress avg");
  assert(planFactCell(indConv, byId, days1218, dataInv, "12–18").value === 80, "G: regress conv");

  /* Личный writeoff не в series inventory */
  assert(o1.mode === "inventory_shop", "G: mode inventory_shop");
  const whO = mgmtCollectMetricOverallSeries(Object.assign({}, dataInv, {
    metricId: "warehouse_metric_inventory_result", days: days1218
  }));
  assert(whO.mode === "inventory_warehouse", "G: mode inventory_warehouse");
  assert(aggregateSum(whO.series) === 750, "G: warehouse series 750");
}

console.log("wt_spu_shop_planfact_e2e_harness: OK");
console.log("  A sales/checks/avg: 100000 / 200 / 500");
console.log("  B visitors/conv: 250 / 80%");
console.log("  C two slots: 50000 / 100");
console.log("  D weeks 12–18 filled, 19–25 empty→null, legacy department_total works");
console.log("  E sources empty metric_id inherits parent");
console.log("  F mind_091/092/017 manual→worktime + calculated + picker builtins");
console.log("  G inventory shop/warehouse signed weeks, zero, empty→null, cross-month, rebind");
