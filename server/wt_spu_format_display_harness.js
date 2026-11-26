/**
 * SPU stage-2 display formatters — synthetic checks (no DB).
 * Mirrors display-only helpers from app.html (must stay aligned).
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

function mgmtRoundDisplay(n, maxFrac) {
  const x = Number(n);
  if (!Number.isFinite(x)) return NaN;
  const f = Math.pow(10, maxFrac == null ? 2 : maxFrac);
  const s = x < 0 ? -1 : 1;
  let r = s * Math.round(Math.abs(x) * f + 1e-10) / f;
  if (r === 0) r = 0;
  return r;
}

function mgmtFormatDisplayNumber(n, maxFrac) {
  const r = mgmtRoundDisplay(n, maxFrac);
  if (!Number.isFinite(r)) return "—";
  let s = r.toLocaleString("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: maxFrac == null ? 2 : maxFrac
  });
  if (s.charAt(0) === "-") s = "\u2212" + s.slice(1);
  return s;
}

function mgmtFormatMoneyDisplay(n) {
  const r = mgmtRoundDisplay(n, 2);
  if (!Number.isFinite(r)) return "—";
  const isInt = Math.abs(r - Math.round(r)) < 1e-9;
  let s = r.toLocaleString("ru-RU", {
    minimumFractionDigits: isInt ? 0 : 2,
    maximumFractionDigits: isInt ? 0 : 2
  });
  if (s.charAt(0) === "-") s = "\u2212" + s.slice(1);
  return s + " \u20BD";
}

function mgmtFormatPct(pct) {
  if (pct == null || !Number.isFinite(Number(pct))) return "—";
  return mgmtFormatDisplayNumber(pct, 2) + "%";
}

function mgmtFormatValue(it, value, status) {
  if (status === "one_c") return "Источник 1С не подключен";
  if (status === "unfrozen") return "не зафиксировано";
  if (status === "error") return "ошибка периода";
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const unit = it && it.unit;
  if (unit === "money") return mgmtFormatMoneyDisplay(n);
  if (unit === "percent") return mgmtFormatDisplayNumber(n, 2) + "%";
  if (unit === "people" || unit === "number" || unit === "count" || unit === "hours") {
    return mgmtFormatDisplayNumber(n, 2);
  }
  if (unit === "days") return mgmtFormatDisplayNumber(n, 2);
  return mgmtFormatDisplayNumber(n, 2);
}

function normSpaces(s) {
  return String(s).replace(/\u00A0/g, " ").replace(/\u202F/g, " ");
}

function assertMoney(n, expect) {
  const got = normSpaces(mgmtFormatMoneyDisplay(n));
  assert(got === expect, "money " + n + " → " + got + " expected " + expect);
}

/* --- percent precision --- */
assert(mgmtFormatPct(0.45) === "0,45%", "0.45% got " + mgmtFormatPct(0.45));
assert(mgmtFormatPct(0.54) === "0,54%", "0.54% got " + mgmtFormatPct(0.54));
assert(mgmtFormatPct(0.45) !== mgmtFormatPct(0.54), "0.45 and 0.54 must differ");
assert(mgmtFormatPct(21.8) === "21,8%", "21.8");
assert(mgmtFormatPct(78) === "78%", "78");
assert(mgmtFormatPct(103.85) === "103,85%", "103.85");
assert(mgmtFormatPct(-0.004) === "0%", "neg zero display");
assert(mgmtFormatPct(-0.04) === "\u22120,04%", "small neg pct " + mgmtFormatPct(-0.04));

/* --- money control cases --- */
assertMoney(3119705.67, "3 119 705,67 \u20BD");
assertMoney(3119705.675, "3 119 705,68 \u20BD");
assertMoney(1054.26935, "1 054,27 \u20BD");
assertMoney(3105667, "3 105 667 \u20BD");
assertMoney(-14038.675, "\u2212" + "14 038,68 \u20BD");
assertMoney(1.005, "1,01 \u20BD");
assertMoney(1.015, "1,02 \u20BD");

/* --- source number unchanged --- */
const src = 3119705.675;
const _fmt = mgmtFormatMoneyDisplay(src);
assert(src === 3119705.675, "source mutated");

/* --- quantity / units --- */
assert(normSpaces(mgmtFormatValue({ unit: "number" }, 4017)) === "4 017", "qty 4017");
assert(mgmtFormatValue({ unit: "number" }, 12.5) === "12,5", "qty frac");
assert(mgmtFormatValue({ unit: "number" }, 4017).indexOf("\u20BD") === -1, "qty no ruble");
assert(mgmtFormatValue({ unit: "number" }, 4017).indexOf("%") === -1, "qty no pct");
assert(mgmtFormatValue({ unit: "people" }, 12.5) === "12,5", "people keeps frac display");

/* --- empty vs zero --- */
assert(mgmtFormatValue({ unit: "money" }, null) === "—", "null empty");
assert(mgmtFormatValue({ unit: "money" }, "") === "—", "empty string");
assert(normSpaces(mgmtFormatValue({ unit: "money" }, 0)) === "0 \u20BD", "zero money");
assert(mgmtFormatPct(0) === "0%", "zero pct");
assert(mgmtFormatPct(null) === "—", "null pct");

/* --- operand units vs result unit --- */
const moneyOp = mgmtFormatValue({ unit: "money" }, 1054.26935);
const pctDraft = mgmtFormatValue({ unit: "percent" }, 1054.26935);
assert(moneyOp.indexOf("\u20BD") !== -1, "money operand has ruble");
assert(pctDraft.indexOf("%") !== -1, "percent draft has %");
assert(moneyOp.indexOf("%") === -1, "money operand not percent");
assert(pctDraft.indexOf("\u20BD") === -1, "percent not money");

/* --- no *100 scale on percent format --- */
assert(mgmtFormatValue({ unit: "percent" }, 0.45) === "0,45%", "no scale *100");
assert(mgmtFormatValue({ unit: "percent" }, 21.8) === "21,8%", "21.8 stays 21.8");

console.log("OK wt_spu_format_display_harness");
