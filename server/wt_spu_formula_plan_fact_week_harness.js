/**
 * СПУ: операнды fact/plan + week_offset; план-формула без записи в store.
 * Без БД / браузера.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function isoAddDays(iso, n) {
  const p = String(iso).split("-").map(Number);
  const d = new Date(p[0], p[1] - 1, p[2] + Number(n || 0));
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return d.getFullYear() + "-" + mm + "-" + dd;
}

function normalizeTok(tok) {
  const kind = String((tok && tok.value_kind) || "fact") === "plan" ? "plan" : "fact";
  let off = Number(tok && tok.week_offset);
  if (!Number.isFinite(off)) off = 0;
  return { t: "ind", id: String((tok && tok.id) || ""), value_kind: kind, week_offset: Math.trunc(off) };
}

function kindKey(tok) {
  const n = normalizeTok(tok);
  return "ind_" + n.value_kind + "_" + (n.week_offset === -1 ? "m1" : "0");
}

function fromKindKey(key, id) {
  if (key === "ind_fact_m1") return normalizeTok({ id: id, value_kind: "fact", week_offset: -1 });
  if (key === "ind_plan_0") return normalizeTok({ id: id, value_kind: "plan", week_offset: 0 });
  if (key === "ind_plan_m1") return normalizeTok({ id: id, value_kind: "plan", week_offset: -1 });
  return normalizeTok({ id: id, value_kind: "fact", week_offset: 0 });
}

function operandWeekFrom(anchor, off) {
  if (!off) return anchor;
  return isoAddDays(anchor, off * 7);
}

function safeDiv(a, b) {
  if (b === 0) return null;
  const out = a / b;
  return Number.isFinite(out) ? out : null;
}

function evalExpr(tokens, resolve) {
  const values = [];
  const ops = [];
  const prec = { "+": 1, "-": 1, "*": 2, "/": 2 };
  function apply(op) {
    const b = values.pop();
    const a = values.pop();
    if (a == null || b == null) { values.push(null); return; }
    if (op === "+") values.push(a + b);
    else if (op === "-") values.push(a - b);
    else if (op === "*") values.push(a * b);
    else values.push(safeDiv(a, b));
  }
  tokens.forEach(function (tok) {
    if (tok.t === "op") {
      while (ops.length && prec[ops[ops.length - 1]] >= prec[tok.v]) apply(ops.pop());
      ops.push(tok.v);
      return;
    }
    if (tok.t === "num") { values.push(Number(tok.v)); return; }
    if (tok.t === "ind") {
      const n = normalizeTok(tok);
      values.push(resolve(n));
    }
  });
  while (ops.length) apply(ops.pop());
  const out = values[0];
  return out == null || !Number.isFinite(out) ? null : out;
}

assert(kindKey({ t: "ind", id: "a" }) === "ind_fact_0", "legacy → fact0");
assert(kindKey({ t: "ind", id: "a", value_kind: "plan", week_offset: -1 }) === "ind_plan_m1", "plan m1");
assert(fromKindKey("ind_fact_m1", "x").week_offset === -1, "from key");
assert(operandWeekFrom("2026-11-12", -1) === "2026-11-05", "prev week");
assert(operandWeekFrom("2026-01-01", -1) === "2025-12-25", "year boundary");

const store = {
  "2026-11-12|fact|rev": 100,
  "2026-11-05|fact|rev": 80,
  "2026-11-12|plan|rev": 120,
  "2026-11-05|plan|sales": 50
};
const anchor = "2026-11-12";
function resolve(n) {
  const w = operandWeekFrom(anchor, n.week_offset);
  const key = w + "|" + n.value_kind + "|" + n.id;
  return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
}

const mixed = [
  fromKindKey("ind_fact_m1", "rev"),
  { t: "op", v: "*" },
  { t: "num", v: 0.8 }
];
assert(Math.abs(evalExpr(mixed, resolve) - 64) < 1e-9, "prev fact * 0.8");

const mix2 = [
  fromKindKey("ind_plan_0", "rev"),
  { t: "op", v: "-" },
  fromKindKey("ind_fact_m1", "rev")
];
assert(Math.abs(evalExpr(mix2, resolve) - 40) < 1e-9, "plan cur - fact prev");

assert(evalExpr([{ t: "num", v: 1 }, { t: "op", v: "/" }, { t: "num", v: 0 }], resolve) == null, "div0");
assert(evalExpr([fromKindKey("ind_plan_0", "missing")], resolve) == null, "missing");

// plan formula display priority simulation: formula on → formula; off → manual
function planValue(mode, formulaVal, periodVal, manualVal) {
  if (mode === "formula" && formulaVal != null) return formulaVal;
  if (periodVal != null) return periodVal;
  return manualVal;
}
assert(planValue("formula", 77, 10, 20) === 77, "formula display");
assert(planValue("manual", 77, 10, 20) === 10, "period wins when formula off");
assert(planValue("manual", 77, null, 20) === 20, "manual kept");

console.log("wt_spu_formula_plan_fact_week_harness: OK");
