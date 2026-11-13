/**
 * Безопасные версии формул СПУ + черновик 7*AR/REV на контрольной неделе.
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

function spuVersionCoversDay(v, day) {
  if (!v) return false;
  if (v.effective_from && String(v.effective_from) > day) return false;
  if (v.effective_to && String(v.effective_to) < day) return false;
  return true;
}

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
  tokens.forEach(function (tok) {
    if (tok.t === "lp") { ops.push("("); return; }
    if (tok.t === "rp") {
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
  return out == null || !Number.isFinite(Number(out)) ? null : Number(out);
}

function planInsertSegment(vers, from) {
  const covering = vers.filter(function (v) { return spuVersionCoversDay(v, from); });
  assert(covering.length === 1, "one covering");
  const V = covering[0];
  const later = vers.filter(function (v) { return String(v.effective_from) > from; })
    .sort(function (a, b) { return a.effective_from.localeCompare(b.effective_from); });
  assert(later.length >= 1, "has later");
  const nextFrom = later[0].effective_from;
  const segTo = isoAddDays(nextFrom, -1);
  V.effective_to = isoAddDays(from, -1);
  const N = {
    version_id: "sver_new",
    effective_from: from,
    effective_to: segTo,
    formula_expr: "NEW"
  };
  vers.push(N);
  vers.sort(function (a, b) { return a.effective_from.localeCompare(b.effective_from); });
  return { V: V, N: N, next: later[0] };
}

function versionAt(vers, day) {
  let hit = null;
  vers.forEach(function (v) {
    if (spuVersionCoversDay(v, day)) hit = v;
  });
  return hit;
}

// --- eval 7 * AR / REV ---
const tokens = [
  { t: "num", v: 7 },
  { t: "op", v: "*" },
  { t: "ind", id: "ar" },
  { t: "op", v: "/" },
  { t: "ind", id: "rev" }
];
const facts = { ar: 1400000, rev: 2221283.1 };
const result = spuEvalFormula(tokens, function (id) { return facts[id]; });
assert(result != null, "result");
assert(Math.abs(result - (7 * 1400000 / 2221283.1)) < 1e-6, "≈4.41");
assert(spuEvalFormula(tokens, function (id) {
  return id === "ar" ? 1400000 : null;
}) == null, "null operand → null");

// --- insert segment mind_099-like ---
const vers = [
  { version_id: "sver_old", effective_from: "2000-01-01", effective_to: "2026-11-12", formula_expr: "OLD" },
  { version_id: "sver_newday", effective_from: "2026-11-13", effective_to: "", formula_expr: "TODAY" }
];
const applied = planInsertSegment(vers, "2026-11-05");
assert(applied.V.effective_to === "2026-11-04", "old closed before from");
assert(applied.N.effective_from === "2026-11-05", "seg from");
assert(applied.N.effective_to === "2026-11-12", "seg to day before later");
assert(applied.next.effective_from === "2026-11-13", "later intact");
assert(versionAt(vers, "2026-11-11").formula_expr === "NEW", "week end 11.11 → NEW");
assert(versionAt(vers, "2026-11-04").formula_expr === "OLD", "before from → OLD");
assert(versionAt(vers, "2026-11-18").formula_expr === "TODAY", "after later → TODAY");

// no double cover
["2026-11-04", "2026-11-05", "2026-11-12", "2026-11-13"].forEach(function (d) {
  const n = vers.filter(function (v) { return spuVersionCoversDay(v, d); }).length;
  assert(n === 1, "exactly one cover " + d);
});

console.log("wt_spu_formula_week_version_harness: OK");
