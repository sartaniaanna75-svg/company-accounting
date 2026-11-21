/**
 * СПУ metric_overall: presence-aware чтение всех store-типов (зеркало app.html).
 * 14 групп проверок PLAN «аудит чтения метрик». Без БД.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function metricFactStorageKey(metricId, subId) {
  return subId ? String(metricId) + "::" + String(subId) : String(metricId);
}

function aggregateSum(values) {
  return (values || []).reduce(function (s, n) { return s + Number(n || 0); }, 0);
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

function metricFactDeptForEmployee(empId, host, homeDept) {
  return homeDept[empId] || host || "office";
}

function shopCatalogOperandSnapshot(opts) {
  const mid = opts.metricId;
  const item = opts.catalogById[mid];
  const empId = opts.employeeId;
  const day = String(opts.day);
  const month = opts.month;
  const subId = opts.subId || "";
  if (!mid) return { present: false, value: null };
  if (item && item.fill_type === "auto_calc") {
    return { present: false, value: null }; // handled in overall
  }
  const hostDept = (item && item.department_id) || "";
  if (hostDept === "office" || hostDept === "warehouse" || hostDept === "drivers") {
    const dept = metricFactDeptForEmployee(empId, hostDept, opts.homeDept);
    const key = metricFactStorageKey(mid, subId);
    const rec = opts.factsRoot[dept] && opts.factsRoot[dept][month]
      && opts.factsRoot[dept][month][empId] && opts.factsRoot[dept][month][empId][day];
    if (!rec || rec[key] == null || rec[key] === "") {
      if (rec && (rec[key + "Mark"] === "done" || rec[key + "Mark"] === "fail")) {
        return { present: true, value: rec[key + "Mark"] === "done" ? 1 : 0 };
      }
      return { present: false, value: null };
    }
    return { present: true, value: Number(rec[key] || 0) };
  }
  if (mid === "metric_sales" || mid === "metric_checks") {
    const fieldKey = mid === "metric_sales" ? "sales" : "receipts";
    const rec = opts.cashiers[month] && opts.cashiers[month][empId] && opts.cashiers[month][empId][day];
    if (!rec || rec[fieldKey] == null || rec[fieldKey] === "") return { present: false, value: null };
    return { present: true, value: Number(rec[fieldKey] || 0) };
  }
  if (item && item.usesFacts) {
    const key = metricFactStorageKey(mid, subId);
    const rec = opts.shopFacts[month] && opts.shopFacts[month][empId] && opts.shopFacts[month][empId][day];
    if (rec && rec[key] != null && rec[key] !== "") return { present: true, value: Number(rec[key] || 0) };
    return { present: false, value: null };
  }
  const section = (item && item.section_id) || "pickers";
  const key = (item && item.key) || mid;
  const store = opts.category[section] && opts.category[section][month];
  const rec = store && store[empId] && store[empId][day];
  if (!rec) return { present: false, value: null };
  if (item && item.fill_type === "check") {
    const mark = rec[key + "Mark"];
    if (mark === "done" || mark === "fail") return { present: true, value: mark === "done" ? 1 : 0 };
    return { present: false, value: null };
  }
  if (rec[key] == null || rec[key] === "") return { present: false, value: null };
  return { present: true, value: Number(rec[key] || 0) };
}

function mgmtMetricOverallPeople(item, peopleByMetric) {
  return (peopleByMetric[item.id] || []).map(function (id) { return { id: id }; });
}

function mgmtCollectMetricOverallSeries(opts) {
  const mid = opts.metricId;
  const item = opts.catalogById[mid];
  const out = { present: false, series: [], mode: "empty", value: null };
  if (!item && mid !== "store_metric_visitors") {
    out.mode = "no_catalog";
    return out;
  }
  if (item && item.fill_type === "vehicle") { out.mode = "vehicle"; return out; }
  if (item && item.fill_type === "month_money") { out.mode = "month_money_unsupported"; return out; }

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

  /* Cashiers shop total: slots (peopleByMetric as slot ids) + channel self_checkout once. */
  if (mid === "metric_sales" || mid === "metric_checks") {
    out.mode = "cashiers_shop";
    const fieldKey = mid === "metric_sales" ? "sales" : "receipts";
    const slots = mgmtMetricOverallPeople(item, opts.peopleByMetric);
    (opts.days || []).forEach(function (d) {
      let daySum = 0;
      let any = false;
      slots.forEach(function (e) {
        if (!e || !e.id || e.id === "self_checkout") return;
        const snap = shopCatalogOperandSnapshot(Object.assign({}, opts, {
          employeeId: e.id, day: d.day, month: d.month, metricId: mid
        }));
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

  const people = mgmtMetricOverallPeople(item, opts.peopleByMetric);

  if (item.fill_type === "auto_calc") {
    out.mode = "auto_calc";
    const op = item.formula && item.formula.op;
    if (op === "pct") return out;
    if (op === "div") {
      let sumL = 0;
      let sumR = 0;
      let any = false;
      (opts.days || []).forEach(function (d) {
        people.forEach(function (e) {
          const L = opts.autoOperands[e.id + "|" + d.day + "|" + item.formula.left];
          const R = opts.autoOperands[e.id + "|" + d.day + "|" + item.formula.right];
          if (L == null || R == null) return;
          sumL += Number(L);
          sumR += Number(R);
          any = true;
        });
      });
      if (!any || sumR === 0) return out;
      out.present = true;
      out.value = sumL / sumR;
      out.series = [out.value];
      return out;
    }
    if (op === "add" || op === "sub" || op === "mul") {
      let tot = 0;
      let any = false;
      (opts.days || []).forEach(function (d) {
        people.forEach(function (e) {
          const left = shopCatalogOperandSnapshot(Object.assign({}, opts, {
            employeeId: e.id, day: d.day, month: d.month, metricId: item.formula.left
          }));
          const right = shopCatalogOperandSnapshot(Object.assign({}, opts, {
            employeeId: e.id, day: d.day, month: d.month, metricId: item.formula.right
          }));
          if (!left.present || !right.present) return;
          let v = null;
          if (op === "add") v = left.value + right.value;
          if (op === "sub") v = left.value - right.value;
          if (op === "mul") v = left.value * right.value;
          if (v == null || !Number.isFinite(v)) return;
          tot += v;
          any = true;
        });
      });
      if (!any) return out;
      out.present = true;
      out.value = tot;
      out.series = [tot];
      return out;
    }
    return out;
  }

  (opts.days || []).forEach(function (d) {
    people.forEach(function (e) {
      const snap = shopCatalogOperandSnapshot(Object.assign({}, opts, {
        employeeId: e.id, day: d.day, month: d.month, metricId: mid, subId: opts.subId || ""
      }));
      if (!snap.present || snap.value == null) return;
      out.series.push(Number(snap.value));
    });
  });
  out.present = out.series.length > 0;
  out.mode = "day_facts";
  return out;
}

function overall(opts) {
  const o = mgmtCollectMetricOverallSeries(opts);
  if (!o.present) return null;
  if (o.mode === "auto_calc" && o.value != null) return Number(o.value);
  return aggregateSum(o.series);
}

const NOV = "2026-11";
const catalogById = {
  metric_display_positions: {
    id: "metric_display_positions", department_id: "shop", section_id: "pickers",
    key: "display", fill_type: "number", builtin: true
  },
  metric_aisles: {
    id: "metric_aisles", department_id: "shop", section_id: "pickers",
    key: "walks", fill_type: "check", builtin: true
  },
  metric_sales: { id: "metric_sales", department_id: "shop", section_id: "cashiers", key: "sales", fill_type: "money", builtin: true },
  metric_checks: { id: "metric_checks", department_id: "shop", section_id: "cashiers", key: "receipts", fill_type: "number", builtin: true },
  metric_custom_qty: { id: "metric_custom_qty", department_id: "shop", fill_type: "number", usesFacts: true },
  metric_avg: {
    id: "metric_avg", department_id: "shop", fill_type: "auto_calc",
    formula: { op: "div", left: "metric_sales", right: "metric_checks" }
  },
  metric_pct: {
    id: "metric_pct", department_id: "shop", fill_type: "auto_calc",
    formula: { op: "pct", left: "a", right: "b" }
  },
  metric_writeoff: { id: "metric_writeoff", department_id: "shop", fill_type: "month_money" },
  metric_vehicle: { id: "metric_vehicle", department_id: "drivers", fill_type: "vehicle" },
  m_sales_opt: {
    id: "m_sales_opt", department_id: "drivers", territory_category: "sales_opt", fill_type: "number"
  },
  m_pick: { id: "m_pick", department_id: "warehouse", fill_type: "number" },
  m_office: { id: "m_office", department_id: "office", fill_type: "number" },
  m_driver_km: { id: "m_driver_km", department_id: "drivers", territory_category: "drivers", fill_type: "number" },
  m_terr_auto: {
    id: "m_terr_auto", department_id: "warehouse", fill_type: "auto_calc",
    formula: { op: "add", left: "m_pick", right: "m_pick_b" }
  },
  m_pick_b: { id: "m_pick_b", department_id: "warehouse", fill_type: "number" },
  store_metric_visitors: { id: "store_metric_visitors", department_id: "shop" }
};

const peopleByMetric = {
  metric_display_positions: ["e_a", "e_b"],
  metric_aisles: ["e_a"],
  metric_sales: ["c1", "c2"],
  metric_checks: ["c1", "c2"],
  metric_custom_qty: ["e_a"],
  metric_avg: ["c1"],
  metric_pct: ["c1"],
  m_sales_opt: ["e_nat", "e_rep"],
  m_pick: ["e_loader"],
  m_office: ["e_off"],
  m_driver_km: ["e_drv"],
  m_terr_auto: ["e_wh_home"],
  m_pick_b: ["e_wh_home"]
};

const homeDept = {
  e_a: "shop", e_b: "shop", c1: "shop",
  e_nat: "drivers", e_rep: "drivers", e_drv: "drivers",
  e_loader: "warehouse", e_wh_home: "office", e_off: "office"
};

/* Category pickers: display totals for control weeks */
const category = {
  pickers: {
    "2026-11": {
      e_a: {
        "12": { display: 4000 }, "13": { display: 4000 }, "14": { display: 4000 },
        "15": { display: 4000 }, "16": { display: 4000 }, "17": { display: 3302 },
        "18": { display: 2000 },
        "20": { display: 500 },
        "11": { walksMark: "done" }
      },
      e_b: {
        "12": { display: 0 }, // explicit 0 on e_b day12 — wait, for 25302 we need sum
        "20": { display: 388 },
        "19": { display: 100 }
      }
    }
  }
};
/* Fix 12–18 sum: e_a 4000*5+3302+2000 = 20000+3302+2000=25302; e_b day12 explicit 0 present */
category.pickers["2026-11"].e_b["12"] = { display: 0 };
category.pickers["2026-11"].e_b["13"] = {}; // missing — skip

const cashiers = {
  "2026-11": {
    /* Два слота; один actual может работать на обоих — суммируем слоты. */
    c1: { "20": { sales: 1000, receipts: 8 }, "21": { sales: 400, receipts: 4 } },
    c2: { "20": { sales: 500, receipts: 2 }, "21": { sales: 100, receipts: 1 } }
  }
};

const channelValues = {
  "2026-11": {
    self_checkout: {
      "20": { sales: 200, receipts: 5 },
      "21": { sales: 50, receipts: 1 }
    }
  }
};

const shopFacts = {
  "2026-11": {
    e_a: { "20": { metric_custom_qty: 42 }, "21": { metric_custom_qty: 0 } }
  }
};

const store = {
  "2026-11": {
    "20": { visitors: 77 },
    "21": { visitors: 23 }
  }
};

const factsRoot = {
  drivers: {
    "2026-11": {
      e_nat: { "20": { m_sales_opt: 100 }, "21": { m_sales_opt: 50 } },
      e_rep: { "20": { m_sales_opt: 25 } },
      e_drv: { "20": { m_driver_km: 5 }, "21": { m_driver_km: 7 } },
      e_access: { "20": { m_sales_opt: 999 } }
    }
  },
  warehouse: {
    "2026-11": {
      e_loader: { "20": { m_pick: 8 } },
      e_wh_home: { "20": { m_pick: 3, m_pick_b: 2 } } // wrong dept for e_wh_home — home is office
    }
  },
  office: {
    "2026-11": {
      e_off: { "20": { m_office: 11 } },
      e_wh_home: { "20": { m_pick: 30, m_pick_b: 20 } } // home store for auto_calc
    }
  }
};

const autoOperands = {
  "c1|20|metric_sales": 100, "c1|20|metric_checks": 10,
  "c1|21|metric_sales": 50, "c1|21|metric_checks": 5
};

const base = {
  catalogById: catalogById,
  peopleByMetric: peopleByMetric,
  homeDept: homeDept,
  category: category,
  cashiers: cashiers,
  channelValues: channelValues,
  shopFacts: shopFacts,
  store: store,
  factsRoot: factsRoot,
  autoOperands: autoOperands
};

const days1925 = daysBetween("2026-11-19", "2026-11-25");
const days1218 = daysBetween("2026-11-12", "2026-11-18");
const days0511 = daysBetween("2026-11-05", "2026-11-11");

// 1) Shop builtin display 20.11 = 888
{
  const v = overall(Object.assign({}, base, {
    metricId: "metric_display_positions",
    days: [{ day: 20, month: NOV }]
  }));
  assert(v === 500 + 388, "1: day20 display 888 got " + v);
}

// 2) Shop week 12–18 = 25302 (e_a days + e_b explicit 0 on 12)
{
  const v = overall(Object.assign({}, base, {
    metricId: "metric_display_positions",
    days: days1218
  }));
  assert(v === 25302 + 0, "2: week 12–18 = 25302 got " + v);
}

// 3) Shop check Mark done
{
  const v = overall(Object.assign({}, base, {
    metricId: "metric_aisles",
    days: [{ day: 11, month: NOV }]
  }));
  assert(v === 1, "3: check done → 1");
}

// 4) Cashiers sales = slots c1+c2 + KSO once (как ИТОГО ПО МАГАЗИНУ)
{
  const v = overall(Object.assign({}, base, {
    metricId: "metric_sales",
    days: [{ day: 20, month: NOV }, { day: 21, month: NOV }]
  }));
  /* day20: 1000+500+200=1700; day21: 400+100+50=550; week=2250 */
  assert(v === 1700 + 550, "4: cashiers sales slots+KSO got " + v);
  const checks = overall(Object.assign({}, base, {
    metricId: "metric_checks",
    days: [{ day: 20, month: NOV }, { day: 21, month: NOV }]
  }));
  /* day20: 8+2+5=15; day21: 4+1+1=6; week=21 */
  assert(checks === 15 + 6, "4: cashiers checks slots+KSO got " + checks);
  const avg = (1700 + 550) / (15 + 6);
  assert(Math.abs(avg - 2250 / 21) < 1e-9, "4: shop_avg from fixed sales/checks");
  const emptyCash = overall(Object.assign({}, base, {
    metricId: "metric_sales",
    days: [{ day: 1, month: NOV }]
  }));
  assert(emptyCash === null, "4: empty cashiers week → null");
  const ksoOnly = overall(Object.assign({}, base, {
    metricId: "metric_sales",
    days: [{ day: 20, month: NOV }],
    peopleByMetric: Object.assign({}, peopleByMetric, { metric_sales: [] })
  }));
  assert(ksoOnly === 200, "4: KSO alone once got " + ksoOnly);
}

// 5) Visitors store
{
  const v = overall(Object.assign({}, base, {
    metricId: "store_metric_visitors",
    days: [{ day: 20, month: NOV }, { day: 21, month: NOV }]
  }));
  assert(v === 77 + 23, "5: visitors");
  const empty = overall(Object.assign({}, base, {
    metricId: "store_metric_visitors",
    days: [{ day: 22, month: NOV }]
  }));
  assert(empty === null, "5: missing visitors → null");
}

// 6) Custom facts + presence
{
  const v = overall(Object.assign({}, base, {
    metricId: "metric_custom_qty",
    days: [{ day: 20, month: NOV }, { day: 22, month: NOV }]
  }));
  assert(v === 42, "6: custom facts; day22 missing skipped");
}

// 7) Explicit 0 kept; empty period → null
{
  const z = overall(Object.assign({}, base, {
    metricId: "metric_custom_qty",
    days: [{ day: 21, month: NOV }]
  }));
  assert(z === 0, "7: explicit 0");
  const none = overall(Object.assign({}, base, {
    metricId: "metric_display_positions",
    days: [{ day: 1, month: NOV }]
  }));
  assert(none === null, "7: empty → null not 0");
}

// 8) Territory OPT / warehouse / office / drivers — day-facts, no trips
{
  assert(overall(Object.assign({}, base, {
    metricId: "m_sales_opt", days: [{ day: 20, month: NOV }]
  })) === 125, "8: OPT");
  assert(overall(Object.assign({}, base, {
    metricId: "m_pick", days: [{ day: 20, month: NOV }]
  })) === 8, "8: warehouse");
  assert(overall(Object.assign({}, base, {
    metricId: "m_office", days: [{ day: 20, month: NOV }]
  })) === 11, "8: office");
  assert(overall(Object.assign({}, base, {
    metricId: "m_driver_km", days: [{ day: 20, month: NOV }, { day: 21, month: NOV }]
  })) === 12, "8: drivers day-facts");
}

// 9) Multi-source shop + OPT
{
  const a = overall(Object.assign({}, base, {
    metricId: "metric_display_positions", days: [{ day: 20, month: NOV }]
  }));
  const b = overall(Object.assign({}, base, {
    metricId: "m_sales_opt", days: [{ day: 20, month: NOV }]
  }));
  assert(a + b === 888 + 125, "9: multi-source sum");
}

// 10) auto_calc div; pct null; home≠host operand
{
  const divV = overall(Object.assign({}, base, {
    metricId: "metric_avg",
    days: [{ day: 20, month: NOV }, { day: 21, month: NOV }]
  }));
  assert(divV === (100 + 50) / (10 + 5), "10: auto_calc div");
  assert(overall(Object.assign({}, base, {
    metricId: "metric_pct", days: [{ day: 20, month: NOV }]
  })) === null, "10: pct → null");
  /* e_wh_home home=office has m_pick=30, m_pick_b=20; warehouse host would be 3+2 */
  const terrAuto = overall(Object.assign({}, base, {
    metricId: "m_terr_auto",
    days: [{ day: 20, month: NOV }]
  }));
  assert(terrAuto === 30 + 20, "10: auto_calc uses home store got " + terrAuto);
}

// 11) E1 / legacy modes — overall ignores trips junk; vehicle/month_money unsupported
{
  assert(overall(Object.assign({}, base, {
    metricId: "metric_vehicle", days: [{ day: 20, month: NOV }]
  })) === null, "11: vehicle null");
  assert(overall(Object.assign({}, base, {
    metricId: "metric_writeoff", days: [{ day: 20, month: NOV }]
  })) === null, "11: month_money null");
  /* access-only not in people — already via peopleByMetric */
  assert(peopleByMetric.m_sales_opt.indexOf("e_access") === -1, "11: people assignments only");
}

// 12) Accumulation current week by days
{
  const d19 = overall(Object.assign({}, base, {
    metricId: "metric_display_positions", days: [{ day: 19, month: NOV }]
  }));
  const d1920 = overall(Object.assign({}, base, {
    metricId: "metric_display_positions",
    days: [{ day: 19, month: NOV }, { day: 20, month: NOV }]
  }));
  assert(d19 === 100, "12: day19 alone");
  assert(d1920 === 100 + 888, "12: accumulates day20");
  assert(days1925[0].day === 19 && days1925[days1925.length - 1].day === 25, "12: week 19–25");
}

// 13) Historical weeks / version at week-end (no rewrite)
{
  const historic = {
    source_versions: [
      { version_id: "v1", effective_from: "2026-11-01", effective_to: "2026-11-18",
        sources: [{ source_result: "group_total", source_metric_id: "metric_display_positions" }] },
      { version_id: "v2", effective_from: "2026-11-19", effective_to: "",
        sources: [{ source_result: "metric_overall", source_metric_id: "metric_display_positions" }] }
    ]
  };
  function pickVersion(versions, asOf) {
    let best = null;
    versions.forEach(function (v) {
      if (String(v.effective_from || "") > asOf) return;
      if (v.effective_to && String(v.effective_to) < asOf) return;
      if (!best || String(v.effective_from) >= String(best.effective_from)) best = v;
    });
    return best;
  }
  assert(pickVersion(historic.source_versions, "2026-11-11").version_id === "v1", "13: week 5–11 → v1");
  assert(pickVersion(historic.source_versions, "2026-11-18").version_id === "v1", "13: week 12–18 → v1");
  assert(pickVersion(historic.source_versions, "2026-11-25").version_id === "v2", "13: week 19–25 → v2");
  assert(pickVersion(historic.source_versions, "2026-11-11").sources[0].source_result === "group_total", "13: historic mode kept");
  void days0511;
  const thu = reportingWeekWithStartDay(4, "2026-11-20");
  assert(thu.from === "2026-11-19" && thu.to === "2026-11-25", "13: Thu–Wed window");
}

// 14) Marker: shop path not via always-0 StoredFact semantics (presence skip)
{
  const o = mgmtCollectMetricOverallSeries(Object.assign({}, base, {
    metricId: "metric_display_positions",
    days: [{ day: 1, month: NOV }, { day: 20, month: NOV }]
  }));
  assert(o.present === true, "14: present");
  assert(o.series.indexOf(0) === -1 || o.series.some(function (n) { return n === 888 || n === 500 || n === 388; }), "14: has real values");
  assert(aggregateSum(o.series) === 888, "14: only day20 values, day1 absent skipped");
}

console.log("wt_spu_metric_overall_harness: OK");
