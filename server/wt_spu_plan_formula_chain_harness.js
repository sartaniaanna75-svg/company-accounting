/**
 * СПУ этап 1: цепочки formulа-планов, ключ id|kind|weekStartIso, без ложного цикла.
 * Синтетика без БД / браузера. Зеркало контракта spuReadFormulaIndOperand + mgmtIndicatorPlan.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

const SPU_FORMULA_EVAL_MAX_DEPTH = 16;

function isoAddDays(iso, n) {
  const p = String(iso).split("-").map(Number);
  const d = new Date(p[0], p[1] - 1, p[2] + Number(n || 0));
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return d.getFullYear() + "-" + mm + "-" + dd;
}

function nodeKey(id, kind, weekIso) {
  return String(id || "") + "|" + String(kind || "fact") + "|" + String(weekIso || "");
}

function trailDepth(trail) {
  let n = 0;
  (trail || []).forEach(function (k) {
    if (typeof k === "string" && k.indexOf("|") !== -1) n += 1;
  });
  return n;
}

function safeDiv(a, b) {
  if (b === 0) return null;
  const out = a / b;
  return Number.isFinite(out) ? out : null;
}

function evalTokens(tokens, resolve) {
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
  (tokens || []).forEach(function (tok) {
    if (!tok) return;
    if (tok.t === "lp") { ops.push("("); return; }
    if (tok.t === "rp") {
      while (ops.length && ops[ops.length - 1] !== "(") apply(ops.pop());
      ops.pop();
      return;
    }
    if (tok.t === "op") {
      while (ops.length && ops[ops.length - 1] !== "(" && (prec[ops[ops.length - 1]] || 0) >= (prec[tok.v] || 1)) {
        apply(ops.pop());
      }
      ops.push(tok.v);
      return;
    }
    if (tok.t === "num") { values.push(Number(tok.v)); return; }
    if (tok.t === "ind") values.push(resolve(tok));
  });
  while (ops.length) {
    if (ops[ops.length - 1] === "(") ops.pop();
    else apply(ops.pop());
  }
  const out = values.length ? values[0] : null;
  return out == null || !Number.isFinite(out) ? null : out;
}

function makeEngine(indicators, manualPlans) {
  let memo = {};
  let anchor = "2026-11-19";

  function weekOf(off) {
    return isoAddDays(anchor, (Number(off) || 0) * 7);
  }

  function indicatorPlan(it, trail) {
    const t = trail || [];
    if (trailDepth(t) >= SPU_FORMULA_EVAL_MAX_DEPTH) return null;
    const mode = it.plan_calc_mode;
    const expr = it.plan_formula_expr || [];
    if (mode === "formula" && expr.length) {
      const selfKey = nodeKey(it.indicator_id, "plan", weekOf(0));
      if (t.indexOf(selfKey) !== -1) return null;
      return evalTokens(expr, function (tok) {
        return readOperand(it, tok, t.concat([selfKey]));
      });
    }
    const w = weekOf(0);
    const mp = manualPlans[w] && manualPlans[w][it.indicator_id];
    return mp == null || !Number.isFinite(Number(mp)) ? null : Number(mp);
  }

  function readOperand(selfIt, tok, trail) {
    const id = String(tok.id || "");
    const kind = String(tok.value_kind || "fact") === "plan" ? "plan" : "fact";
    const off = Number(tok.week_offset) || 0;
    if (!id) return null;
    if (trailDepth(trail) >= SPU_FORMULA_EVAL_MAX_DEPTH) return null;
    const target = weekOf(off);
    const key = nodeKey(id, kind, target);
    if ((trail || []).indexOf(key) !== -1) return null;
    if (Object.prototype.hasOwnProperty.call(memo, key)) return memo[key];

    const other = indicators[id];
    if (!other) {
      memo[key] = null;
      return null;
    }

    let out = null;
    const saved = anchor;
    if (off) anchor = target;
    try {
      if (kind === "plan") {
        // Без пред-push key — как исправленный app.html
        out = indicatorPlan(other, trail || []);
      } else {
        out = null;
      }
    } finally {
      anchor = saved;
    }
    memo[key] = out == null || !Number.isFinite(Number(out)) ? null : Number(out);
    return memo[key];
  }

  function planOf(id) {
    memo = {};
    return indicatorPlan(indicators[id], []);
  }

  function planOfOrder(ids) {
    memo = {};
    const out = {};
    ids.forEach(function (id) {
      out[id] = indicatorPlan(indicators[id], []);
    });
    return out;
  }

  return { planOf: planOf, planOfOrder: planOfOrder, setAnchor: function (w) { anchor = w; } };
}

// --- Контрольный финансовый пример 19.11.2026–25.11.2026 ---
const WEEK = "2026-11-19";
const A_PLAN = 3105667;
const inds = {
  mind_a: {
    indicator_id: "mind_a",
    name: "Выручка сумма отгруженного товара ОПТ",
    plan_calc_mode: "manual",
    plan_formula_expr: []
  },
  mind_b: {
    indicator_id: "mind_b",
    name: "Переданные клиенту по накладной Склад/Опт",
    plan_calc_mode: "formula",
    plan_formula_expr: [
      { t: "ind", id: "mind_a", value_kind: "plan", week_offset: 0 }
    ]
  },
  mind_c: {
    indicator_id: "mind_c",
    name: "Сумма собранных заявок на складе Склад/Опт",
    plan_calc_mode: "formula",
    // Plan(B) ÷ (1 − 0.45 ÷ 100)
    plan_formula_expr: [
      { t: "ind", id: "mind_b", value_kind: "plan", week_offset: 0 },
      { t: "op", v: "/" },
      { t: "lp", v: "(" },
      { t: "num", v: 1 },
      { t: "op", v: "-" },
      { t: "num", v: 0.45 },
      { t: "op", v: "/" },
      { t: "num", v: 100 },
      { t: "rp", v: ")" }
    ]
  },
  mind_d: {
    indicator_id: "mind_d",
    plan_calc_mode: "formula",
    plan_formula_expr: [
      { t: "ind", id: "mind_b", value_kind: "plan", week_offset: 0 }
    ]
  },
  mind_cycle_x: {
    indicator_id: "mind_cycle_x",
    plan_calc_mode: "formula",
    plan_formula_expr: [
      { t: "ind", id: "mind_cycle_y", value_kind: "plan", week_offset: 0 }
    ]
  },
  mind_cycle_y: {
    indicator_id: "mind_cycle_y",
    plan_calc_mode: "formula",
    plan_formula_expr: [
      { t: "ind", id: "mind_cycle_x", value_kind: "plan", week_offset: 0 }
    ]
  },
  mind_prev: {
    indicator_id: "mind_prev",
    plan_calc_mode: "formula",
    plan_formula_expr: [
      { t: "ind", id: "mind_prev", value_kind: "plan", week_offset: -1 }
    ]
  }
};

const manuals = {};
manuals[WEEK] = { mind_a: A_PLAN };
// База для prev-week на глубокой цепочке — нет → depth stop
for (let i = 1; i <= 20; i++) {
  manuals[isoAddDays(WEEK, -7 * i)] = {};
}

const eng = makeEngine(inds, manuals);
eng.setAnchor(WEEK);

const planA = eng.planOf("mind_a");
assert(planA === A_PLAN, "A manual plan");

const planB = eng.planOf("mind_b");
assert(planB === A_PLAN, "B formula plan = A");

const planC = eng.planOf("mind_c");
const expectedC = A_PLAN / (1 - 0.45 / 100);
assert(planC != null && Math.abs(planC - expectedC) < 0.01, "C ≈ 3119705.68 got " + planC);
assert(Math.abs(planC - 3119705.68) < 0.1, "C financial control");

// Порядок C затем B — тот же результат
const order1 = eng.planOfOrder(["mind_c", "mind_b", "mind_a"]);
assert(Math.abs(order1.mind_c - expectedC) < 0.01, "order C-first");
assert(order1.mind_b === A_PLAN, "order C-first B");
const order2 = eng.planOfOrder(["mind_a", "mind_b", "mind_c"]);
assert(Math.abs(order2.mind_c - expectedC) < 0.01, "order A-first");

// Общий источник B на C и D
const shared = eng.planOfOrder(["mind_c", "mind_d"]);
assert(Math.abs(shared.mind_c - expectedC) < 0.01, "shared C");
assert(shared.mind_d === A_PLAN, "shared D = B");

// True cycle
assert(eng.planOf("mind_cycle_x") == null, "cycle x null");
assert(eng.planOf("mind_cycle_y") == null, "cycle y null");

// Бесконечный prev-week без базы → null по лимиту, не hang
assert(eng.planOf("mind_prev") == null, "prev-week depth stop");

// Ноль vs null
manuals[WEEK].mind_a = 0;
assert(eng.planOf("mind_b") === 0, "zero plan propagates");
manuals[WEEK].mind_a = A_PLAN;

// Скобки и приоритет: 1 - 0.45/100 уже в C; отдельно 2+3*4
assert(evalTokens([
  { t: "num", v: 2 }, { t: "op", v: "+" }, { t: "num", v: 3 }, { t: "op", v: "*" }, { t: "num", v: 4 }
], function () { return null; }) === 14, "precedence");

assert(evalTokens([
  { t: "lp" }, { t: "num", v: 2 }, { t: "op", v: "+" }, { t: "num", v: 3 }, { t: "rp" },
  { t: "op", v: "*" }, { t: "num", v: 4 }
], function () { return null; }) === 20, "parens");

console.log("wt_spu_plan_formula_chain_harness: OK");
console.log("control C=", planC, "expected≈", expectedC);
