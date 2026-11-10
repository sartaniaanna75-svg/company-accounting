/**
 * Per-indicator fact week: Thu–Wed default; Wed–Tue only shifts fact days.
 * Plans/period_key stay on global calendar. Formula period conflict detection.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

function mgmtPad(n) { return (n < 10 ? "0" : "") + n; }

function mgmtIsoFromDate(d) {
  return d.getFullYear() + "-" + mgmtPad(d.getMonth() + 1) + "-" + mgmtPad(d.getDate());
}

function mgmtParseIso(iso) {
  const p = String(iso || "").split("-").map(Number);
  return new Date(p[0], (p[1] || 1) - 1, p[2] || 1);
}

function mgmtFmtRu(iso) {
  const p = String(iso || "").split("-");
  if (p.length < 3) return iso || "";
  return p[2] + "." + p[1] + "." + p[0];
}

let GLOBAL_WEEK_START = 4;

function mgmtSpuWeekStartDay() { return GLOBAL_WEEK_START; }

function mgmtSpuReportingWeekWithStartDay(startDay, iso) {
  const d = mgmtParseIso(iso);
  const sd = Number(startDay);
  const start = (Number.isFinite(sd) && sd >= 0 && sd <= 6) ? sd : 4;
  const offset = (d.getDay() - start + 7) % 7;
  const from = new Date(d.getFullYear(), d.getMonth(), d.getDate() - offset);
  const to = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 6);
  const fromIso = mgmtIsoFromDate(from);
  const toIso = mgmtIsoFromDate(to);
  return {
    start_date: fromIso,
    end_date: toIso,
    period_key: "week:" + fromIso,
    from: fromIso,
    to: toIso,
    week_start_day: start
  };
}

function mgmtSpuWeekStartDayFor(it) {
  const per = String((it && it.periodicity) || "week");
  if (per !== "week") return mgmtSpuWeekStartDay();
  if (it && it.reporting_week_start_day != null && it.reporting_week_start_day !== "") {
    const n = Number(it.reporting_week_start_day);
    if (n === 3) return 3;
    if (n === 4) return 4;
  }
  return mgmtSpuWeekStartDay();
}

function mgmtSpuWeekStartLabel(day) {
  return Number(day) === 3 ? "Среда–вторник" : "Четверг–среда";
}

function mgmtDaysBetweenIso(startIso, endIso) {
  const out = [];
  const cur = mgmtParseIso(startIso);
  const end = mgmtParseIso(endIso);
  while (cur <= end) {
    out.push(mgmtIsoFromDate(cur));
    cur.setDate(cur.getDate() + 1);
  }
  return out;
}

let ANCHOR = "2026-11-05"; // Thursday
let PERIOD_KIND = "week";

function mgmtPeriodRange() {
  const w = mgmtSpuReportingWeekWithStartDay(mgmtSpuWeekStartDay(), ANCHOR);
  return { kind: PERIOD_KIND, from: w.from, to: w.to };
}

function mgmtDaysInPeriod() {
  const r = mgmtPeriodRange();
  return mgmtDaysBetweenIso(r.from, r.to);
}

function mgmtFactDaysForIndicator(it) {
  if (PERIOD_KIND !== "week") return mgmtDaysInPeriod();
  const startDay = mgmtSpuWeekStartDayFor(it);
  if (startDay === mgmtSpuWeekStartDay()) return mgmtDaysInPeriod();
  const r = mgmtPeriodRange();
  const w = mgmtSpuReportingWeekWithStartDay(startDay, r.from);
  return mgmtDaysBetweenIso(w.start_date, w.end_date);
}

function mgmtIndicatorFactWindow(it, anchorIso) {
  const day = mgmtSpuWeekStartDayFor(it);
  const w = mgmtSpuReportingWeekWithStartDay(day, anchorIso || ANCHOR);
  return {
    start_date: w.start_date,
    end_date: w.end_date,
    week_start_day: day,
    label: mgmtSpuWeekStartLabel(day),
    rangeRu: mgmtFmtRu(w.start_date) + "–" + mgmtFmtRu(w.end_date)
  };
}

const indicators = {
  a: { indicator_id: "a", name: "Собрано", periodicity: "week", reporting_week_start_day: 3 },
  b: { indicator_id: "b", name: "Обработано", periodicity: "week", reporting_week_start_day: 3 },
  c: { indicator_id: "c", name: "Обычный", periodicity: "week", reporting_week_start_day: 4 },
  nested: {
    indicator_id: "nested",
    name: "Вложенный %",
    periodicity: "week",
    reporting_week_start_day: 3,
    formula_expr: [{ t: "ind", id: "a" }, { t: "op", v: "/" }, { t: "ind", id: "b" }]
  },
  bad: {
    indicator_id: "bad",
    name: "Смесь",
    periodicity: "week",
    reporting_week_start_day: 3,
    formula_expr: [{ t: "ind", id: "a" }, { t: "op", v: "/" }, { t: "ind", id: "c" }]
  }
};

const appData = { management: { indicators: Object.keys(indicators).map(function (k) { return indicators[k]; }) } };

function spuCollectFormulaIndicatorIds(tokens, out, seen, depth) {
  const list = out || [];
  const vis = seen || {};
  const d = depth || 0;
  if (d > 12) return list;
  (tokens || []).forEach(function (tok) {
    if (!tok || tok.t !== "ind" || !tok.id) return;
    if (vis[tok.id]) return;
    vis[tok.id] = true;
    list.push(tok.id);
    const other = indicators[tok.id];
    if (!other) return;
    if (other.formula_expr && other.formula_expr.length) {
      spuCollectFormulaIndicatorIds(other.formula_expr, list, vis, d + 1);
    }
  });
  return list;
}

function spuFormulaFactPeriodConflict(it, tokens, anchorIso) {
  const ids = spuCollectFormulaIndicatorIds(tokens || (it && it.formula_expr) || [], [], {}, 0);
  if (it && it.indicator_id && ids.indexOf(it.indicator_id) === -1) ids.unshift(it.indicator_id);
  if (ids.length < 2) return { ok: true };
  const rows = [];
  ids.forEach(function (id) {
    const ind = indicators[id];
    if (!ind || String(ind.periodicity || "week") !== "week") return;
    const win = mgmtIndicatorFactWindow(ind, anchorIso || ANCHOR);
    rows.push({
      name: ind.name || id,
      start: win.start_date,
      end: win.end_date,
      label: win.label,
      rangeRu: win.rangeRu
    });
  });
  if (rows.length < 2) return { ok: true };
  const key0 = rows[0].start + "|" + rows[0].end;
  if (rows.every(function (row) { return (row.start + "|" + row.end) === key0; })) return { ok: true };
  return {
    ok: false,
    message: rows.map(function (row) {
      return "«" + row.name + "»: " + row.label + " (" + row.rangeRu + ")";
    }).join("\n")
  };
}

// --- assertions ---
const thuWeek = mgmtSpuReportingWeekWithStartDay(4, "2026-11-05");
assert(thuWeek.start_date === "2026-11-05" && thuWeek.end_date === "2026-11-11", "thu week 05-11");

const wedWeek = mgmtSpuReportingWeekWithStartDay(3, "2026-11-05");
assert(wedWeek.start_date === "2026-11-04" && wedWeek.end_date === "2026-11-10", "wed week 04-10 from thu anchor");

const daysThu = mgmtFactDaysForIndicator(indicators.c);
assert(daysThu[0] === "2026-11-05" && daysThu[6] === "2026-11-11", "default fact days thu-wed got " + daysThu[0] + ".." + daysThu[6]);

const daysWed = mgmtFactDaysForIndicator(indicators.a);
assert(daysWed[0] === "2026-11-04" && daysWed[6] === "2026-11-10", "wed fact days got " + daysWed[0] + ".." + daysWed[6]);

assert(daysWed.join(",") !== daysThu.join(","), "fact windows differ");

// Plans key unchanged: still global thu start
assert(thuWeek.period_key === "week:2026-11-05", "plan key stays thu");

const okFormula = spuFormulaFactPeriodConflict(indicators.nested, indicators.nested.formula_expr);
assert(okFormula.ok === true, "same wed operands ok");

const nestedDeep = spuFormulaFactPeriodConflict(
  { indicator_id: "top", name: "Top", periodicity: "week", reporting_week_start_day: 3 },
  [{ t: "ind", id: "nested" }, { t: "op", v: "*" }, { t: "num", v: 100 }]
);
assert(nestedDeep.ok === true, "nested wed chain ok");

const bad = spuFormulaFactPeriodConflict(indicators.bad, indicators.bad.formula_expr);
assert(bad.ok === false, "mixed thu/wed blocked");
assert(String(bad.message).indexOf("Обычный") !== -1, "message names conflict");

PERIOD_KIND = "month";
assert(mgmtFactDaysForIndicator(indicators.a).join(",") === mgmtDaysInPeriod().join(","), "month mode no shift");
PERIOD_KIND = "week";

assert(mgmtSpuWeekStartDayFor({ periodicity: "month", reporting_week_start_day: 3 }) === 4, "month ignores custom");

console.log("OK wt_spu_indicator_week_start_harness");
