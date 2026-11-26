/**
 * SPU stage 2a: PF dual-mode parse/compare/save-gate (synthetic, no DB).
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

function mgmtParsePfCellInput(str) {
  if (str == null) return { ok: true, empty: true, num: null, raw: "" };
  let s = String(str).trim();
  if (s === "") return { ok: true, empty: true, num: null, raw: "" };
  s = s.replace(/\u2212/g, "-").replace(/\u2013/g, "-").replace(/\u2014/g, "-");
  s = s.replace(/[\u00A0\u202F\u2007\s]/g, "");
  s = s.replace(/\u20BD/g, "").replace(/₽/g, "").replace(/%/g, "");
  s = s.replace(/руб\.?/gi, "");
  if (!s || s === "-" || s === "+" || s === "." || s === ",") {
    return { ok: false, error: "Некорректное число" };
  }
  const lastComma = s.lastIndexOf(",");
  const lastDot = s.lastIndexOf(".");
  if (lastComma !== -1 && lastDot !== -1) {
    if (lastComma > lastDot) s = s.replace(/\./g, "").replace(",", ".");
    else s = s.replace(/,/g, "");
  } else if (lastComma !== -1) {
    s = s.replace(",", ".");
  }
  if (!/^[+-]?\d+(\.\d+)?$/.test(s)) return { ok: false, error: "Некорректное число" };
  const n = Number(s);
  if (!Number.isFinite(n)) return { ok: false, error: "Некорректное число" };
  return { ok: true, empty: false, num: n, raw: s };
}

function mgmtPfCellValuesEqual(origVal, parsed) {
  const origEmpty = origVal == null || origVal === "";
  if (!parsed || !parsed.ok) return false;
  if (parsed.empty) return origEmpty;
  if (origEmpty) return false;
  const o = Number(origVal);
  if (!Number.isFinite(o)) return false;
  return o === parsed.num;
}

function mgmtPfUnitForSource(src, parent, indicators) {
  if (!src) return "number";
  const st = String(src.source_type || "");
  if (st === "finance" || st === "spu_indicator" || st === "manual") {
    const oid = src.source_indicator_id;
    if (!oid) return "number";
    const other = (indicators || []).find(function (x) { return x && x.indicator_id === oid; });
    if (other && other.unit) return String(other.unit);
    return "number";
  }
  if (st === "timesheet") return src.source_timesheet_agg === "days" ? "days" : "people";
  if (st === "sales_plan") return src.source_sales_plan_field === "rate" ? "percent" : "money";
  if (st === "worktime_metric") {
    const mid = String(src.source_metric_id || "");
    let found = "";
    (indicators || []).forEach(function (x) {
      if (found || !x || !x.unit) return;
      if (parent && x.indicator_id === parent.indicator_id) return;
      if (String(x.source_metric_id || "") === mid && x.source_type === "worktime_metric") found = String(x.unit);
    });
    return found || "number";
  }
  return "number";
}

/** Pure save-gate used by dual-mode commit (mockable). */
function mgmtPfShouldSave(origRaw, typed, calls) {
  const parsed = mgmtParsePfCellInput(typed);
  if (!parsed.ok) {
    calls.push({ type: "reject", error: parsed.error });
    return false;
  }
  const origVal = origRaw === "" || origRaw == null ? null : origRaw;
  if (mgmtPfCellValuesEqual(origVal, parsed)) {
    calls.push({ type: "skip" });
    return false;
  }
  calls.push({ type: "save", raw: parsed.empty ? "" : parsed.raw, num: parsed.num });
  return true;
}

function simulateEnterThenBlur(origRaw, typed) {
  const calls = [];
  let done = false;
  function commit() {
    if (done) { calls.push({ type: "double" }); return; }
    done = true;
    mgmtPfShouldSave(origRaw, typed, calls);
  }
  commit(); // Enter
  commit(); // blur
  return calls;
}

/* --- parse --- */
assert(mgmtParsePfCellInput("1 054,27 ₽").ok && mgmtParsePfCellInput("1 054,27 ₽").num === 1054.27, "money spaces");
assert(mgmtParsePfCellInput("1\u00A0054,27").num === 1054.27, "nbsp");
assert(mgmtParsePfCellInput("\u221214038,68").num === -14038.68, "unicode minus kept");
assert(mgmtParsePfCellInput("-14038.675").num === -14038.675, "ascii minus");
assert(mgmtParsePfCellInput("0,45%").num === 0.45, "percent no scale");
assert(mgmtParsePfCellInput("1054.26935").num === 1054.26935, "full precision");
assert(mgmtParsePfCellInput("").empty === true, "empty");
assert(mgmtParsePfCellInput("abc").ok === false, "junk");
assert(mgmtParsePfCellInput("Infinity").ok === false, "Infinity");

/* --- equal --- */
assert(mgmtPfCellValuesEqual(1054.26935, mgmtParsePfCellInput("1054.26935")), "unchanged full");
assert(mgmtPfCellValuesEqual(1054.26935, mgmtParsePfCellInput("1 054,26935")), "comma form same");
assert(!mgmtPfCellValuesEqual(1054.26935, mgmtParsePfCellInput("1054.27")), "small change detected");
assert(!mgmtPfCellValuesEqual(null, mgmtParsePfCellInput("0")), "empty != 0");
assert(mgmtPfCellValuesEqual(null, mgmtParsePfCellInput("")), "empty == empty");
assert(mgmtPfCellValuesEqual(-1.5, mgmtParsePfCellInput("\u22121,5")), "neg equal");

/* --- save gate / spy --- */
let calls = [];
assert(!mgmtPfShouldSave("1054.26935", "1054.26935", calls), "no save same");
assert(calls[0].type === "skip", "skip recorded");
calls = [];
assert(mgmtPfShouldSave("1054.26935", "1054.27", calls), "save on change");
assert(calls[0].type === "save" && calls[0].raw === "1054.27", "save raw");
calls = [];
assert(!mgmtPfShouldSave("1054.26935", "abc", calls), "no save junk");
assert(calls[0].type === "reject", "reject recorded");
calls = [];
assert(!mgmtPfShouldSave("100", "100", calls), "formula-plan open/close no save");

const dbl = simulateEnterThenBlur("10", "11");
assert(dbl.filter(function (c) { return c.type === "save"; }).length === 1, "enter+blur one save");
assert(dbl.filter(function (c) { return c.type === "double"; }).length === 1, "second commit blocked");

/* --- breakdown unit: never parent money for checks --- */
const inds = [
  { indicator_id: "avg", unit: "money", source_type: "calculated" },
  { indicator_id: "chk", unit: "number", source_type: "worktime_metric", source_metric_id: "metric_checks" },
  { indicator_id: "rev", unit: "money", source_type: "worktime_metric", source_metric_id: "metric_sales" }
];
const parent = inds[0];
assert(mgmtPfUnitForSource({ source_type: "worktime_metric", source_metric_id: "metric_checks" }, parent, inds) === "number", "checks number");
assert(mgmtPfUnitForSource({ source_type: "worktime_metric", source_metric_id: "metric_sales" }, parent, inds) === "money", "sales money");
assert(mgmtPfUnitForSource({ source_type: "spu_indicator", source_indicator_id: "chk" }, parent, inds) === "number", "spu ind unit");
assert(mgmtPfUnitForSource({ source_type: "spu_indicator", source_indicator_id: "missing" }, parent, inds) === "number", "missing not parent");
assert(mgmtPfUnitForSource({ source_type: "worktime_metric", source_metric_id: "unknown_x" }, parent, inds) === "number", "unknown metric not parent");

console.log("OK wt_spu_pf_edit_format_harness");
