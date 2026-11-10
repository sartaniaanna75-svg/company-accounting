/**
 * Percent formula UX: unit=percent does not *100 on display; precedence needs (1-a/b)*100.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

function spuSafeDiv(a, b) {
  const x = Number(a);
  const y = Number(b);
  if (!Number.isFinite(x) || !Number.isFinite(y) || y === 0) return null;
  const out = x / y;
  return Number.isFinite(out) ? out : null;
}

function spuEvalFormula(tokens, resolve) {
  const values = [];
  const ops = [];
  const prec = { "+": 1, "-": 1, "*": 2, "/": 2, "×": 2, "÷": 2 };
  function applyOp(op) {
    const b = values.pop();
    const a = values.pop();
    if (a == null || b == null) { values.push(null); return; }
    if (op === "+") values.push(a + b);
    else if (op === "-") values.push(a - b);
    else if (op === "*" || op === "×") values.push(a * b);
    else values.push(spuSafeDiv(a, b));
  }
  (tokens || []).forEach(function (tok) {
    if (!tok) return;
    if (tok.t === "lp" || tok.v === "(") { ops.push("("); return; }
    if (tok.t === "rp" || tok.v === ")") {
      while (ops.length && ops[ops.length - 1] !== "(") applyOp(ops.pop());
      ops.pop();
      return;
    }
    if (tok.t === "op") {
      const p = prec[tok.v] || 1;
      while (ops.length && prec[ops[ops.length - 1]] >= p) applyOp(ops.pop());
      ops.push(tok.v);
      return;
    }
    if (tok.t === "num") values.push(Number(tok.v));
    else if (tok.t === "ind") values.push(resolve(tok.id));
  });
  while (ops.length) applyOp(ops.pop());
  const out = values.length ? values[0] : null;
  return (out == null || !Number.isFinite(Number(out))) ? null : Number(out);
}

function mgmtFormatPercent(n) {
  return (Math.round(n * 10) / 10).toLocaleString("ru-RU") + "%";
}

function spuFormulaTokIsLp(tok) { return !!(tok && (tok.t === "lp" || tok.v === "(")); }
function spuFormulaTokIsRp(tok) { return !!(tok && (tok.t === "rp" || tok.v === ")")); }

function spuFormulaHasTrailingTimes100(tokens) {
  if (!tokens || tokens.length < 2) return false;
  const last = tokens[tokens.length - 1];
  const prev = tokens[tokens.length - 2];
  if (!last || last.t !== "num" || Number(last.v) !== 100) return false;
  if (!prev || prev.t !== "op") return false;
  return prev.v === "*" || prev.v === "×";
}

function spuFormulaHasAddSub(tokens) {
  return (tokens || []).some(function (t) {
    return t && t.t === "op" && (t.v === "+" || t.v === "-");
  });
}

function spuFormulaIsOuterParenTimes100(tokens) {
  if (!tokens || tokens.length < 4) return false;
  if (!spuFormulaTokIsLp(tokens[0])) return false;
  if (!spuFormulaHasTrailingTimes100(tokens)) return false;
  if (!spuFormulaTokIsRp(tokens[tokens.length - 3])) return false;
  let depth = 0;
  for (let i = 0; i < tokens.length - 2; i++) {
    if (spuFormulaTokIsLp(tokens[i])) depth += 1;
    else if (spuFormulaTokIsRp(tokens[i])) depth -= 1;
    if (depth < 0) return false;
  }
  return depth === 0;
}

function spuFormulaPercentUxHints(tokens, unit, result) {
  const hints = [];
  if (String(unit || "") !== "percent") return hints;
  const n = Number(result);
  if (Number.isFinite(n) && Math.abs(n) > 0 && Math.abs(n) < 1) {
    hints.push("need times 100");
  }
  if (spuFormulaHasAddSub(tokens) && spuFormulaHasTrailingTimes100(tokens)
      && !spuFormulaIsOuterParenTimes100(tokens)) {
    hints.push("need outer parens");
  }
  return hints;
}

const a = 1304933;
const b = 1235049.1;
const resolve = function (id) { return id === "a" ? a : b; };

const tokShare = [
  { t: "num", v: 1 }, { t: "op", v: "-" },
  { t: "ind", id: "a" }, { t: "op", v: "/" }, { t: "ind", id: "b" }
];
const rawShare = spuEvalFormula(tokShare, resolve);
assert(Math.abs(rawShare + 0.05658) < 0.001, "share raw got " + rawShare);
assert(mgmtFormatPercent(rawShare).indexOf("0,1") !== -1 || mgmtFormatPercent(rawShare).indexOf("-0.1") !== -1
  || String(Math.round(rawShare * 10) / 10) === "-0.1", "format ~ -0.1% got " + mgmtFormatPercent(rawShare));
assert(spuFormulaPercentUxHints(tokShare, "percent", rawShare).indexOf("need times 100") !== -1, "hint for small share");

const tokBad = tokShare.concat([{ t: "op", v: "*" }, { t: "num", v: 100 }]);
const rawBad = spuEvalFormula(tokBad, resolve);
assert(Math.abs(rawBad + 104.658) < 0.05, "bare *100 got " + rawBad);
assert(spuFormulaPercentUxHints(tokBad, "percent", rawBad).indexOf("need outer parens") !== -1, "hint bare *100");

const tokInnerOnly = [
  { t: "num", v: 1 }, { t: "op", v: "-" },
  { t: "lp" }, { t: "ind", id: "a" }, { t: "op", v: "/" }, { t: "ind", id: "b" }, { t: "rp" },
  { t: "op", v: "*" }, { t: "num", v: 100 }
];
const rawInner = spuEvalFormula(tokInnerOnly, resolve);
assert(Math.abs(rawInner + 104.658) < 0.05, "inner-only parens still bad got " + rawInner);
assert(spuFormulaPercentUxHints(tokInnerOnly, "percent", rawInner).indexOf("need outer parens") !== -1, "hint inner-only");

const tokOk = [
  { t: "lp" }, { t: "num", v: 1 }, { t: "op", v: "-" },
  { t: "ind", id: "a" }, { t: "op", v: "/" }, { t: "ind", id: "b" }, { t: "rp" },
  { t: "op", v: "*" }, { t: "num", v: 100 }
];
const rawOk = spuEvalFormula(tokOk, resolve);
assert(Math.abs(rawOk + 5.658) < 0.05, "outer parens ok got " + rawOk);
assert(spuFormulaPercentUxHints(tokOk, "percent", rawOk).length === 0, "no hints for correct formula");
assert(spuFormulaPercentUxHints(tokOk, "number", rawOk).length === 0, "no percent hints for number unit");

// legacy calc_op percent a/b*100
const legacy = spuSafeDiv(a, b) * 100;
assert(Math.abs(legacy - 105.658) < 0.05, "legacy percent ratio");

console.log("OK wt_spu_formula_percent_ux_harness");
