/**
 * СПУ этап 3а: раздельный граф plan/fact для spuWouldCycle.
 * Синтетика без БД / браузера. Зеркало контракта id|kind|woff + версии по неделе.
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

function spuDepNodeKey(indicatorId, kind, weekOffset) {
  const k = String(kind || "fact") === "plan" ? "plan" : "fact";
  let off = Number(weekOffset);
  if (!Number.isFinite(off)) off = 0;
  off = Math.trunc(off);
  return String(indicatorId || "") + "|" + k + "|" + String(off);
}

function spuDepParseNodeKey(nodeKey) {
  const parts = String(nodeKey || "").split("|");
  return {
    id: parts[0] || "",
    kind: parts[1] === "plan" ? "plan" : "fact",
    woff: Number(parts[2]) || 0
  };
}

function spuCfgPlanFormulaActive(cfg, it) {
  const mode = (cfg && cfg.plan_calc_mode) || (it && it.plan_calc_mode);
  const expr = (cfg && cfg.plan_formula_expr) || (it && it.plan_formula_expr) || [];
  return mode === "formula" && Array.isArray(expr) && expr.length > 0;
}

function spuVersionAt(it, iso) {
  const vers = (it && Array.isArray(it.source_versions)) ? it.source_versions : [];
  if (!vers.length) return null;
  const day = iso || "";
  let hit = null;
  vers.forEach(function (v) {
    if (!v) return;
    if (v.effective_from && String(v.effective_from) > day) return;
    if (v.effective_to && String(v.effective_to) < day) return;
    hit = v;
  });
  return hit;
}

function spuConfigRecordFromVersion(it, v) {
  if (v) {
    return {
      sources: v.sources || [],
      formula_expr: Array.isArray(v.formula_expr) ? v.formula_expr : (it.formula_expr || []),
      plan_formula_expr: Array.isArray(v.plan_formula_expr)
        ? v.plan_formula_expr
        : (Array.isArray(it.plan_formula_expr) ? it.plan_formula_expr : []),
      plan_calc_mode: (v.plan_calc_mode === "formula" || it.plan_calc_mode === "formula") ? "formula" : "manual"
    };
  }
  return {
    sources: (it && it.sources) || [],
    formula_expr: (it && it.formula_expr) || [],
    plan_formula_expr: Array.isArray(it && it.plan_formula_expr) ? it.plan_formula_expr : [],
    plan_calc_mode: (it && it.plan_calc_mode) === "formula" ? "formula" : "manual"
  };
}

function normalizeTok(tok, defaultKind) {
  return {
    id: String(tok.id || ""),
    value_kind: String(tok.value_kind || defaultKind) === "plan" ? "plan" : "fact",
    week_offset: Number(tok.week_offset) || 0
  };
}

function spuDepEdgesFromNode(nodeKey, resolveIt, resolveCfg) {
  const parsed = spuDepParseNodeKey(nodeKey);
  if (!parsed.id) return [];
  if (Math.abs(parsed.woff) > SPU_FORMULA_EVAL_MAX_DEPTH) return [];
  const it = resolveIt(parsed.id);
  const cfg = resolveCfg(parsed.id, it, parsed.woff);
  const out = [];
  const pushTok = function (tok, defaultKind) {
    if (!tok || tok.t !== "ind" || !tok.id) return;
    const n = normalizeTok(tok, defaultKind);
    const toOff = parsed.woff + (Number(n.week_offset) || 0);
    if (Math.abs(toOff) > SPU_FORMULA_EVAL_MAX_DEPTH) return;
    out.push(spuDepNodeKey(n.id, n.value_kind || defaultKind, toOff));
  };
  if (parsed.kind === "plan") {
    if (!spuCfgPlanFormulaActive(cfg, it)) return out;
    const expr = (cfg && cfg.plan_formula_expr) || (it && it.plan_formula_expr) || [];
    expr.forEach(function (tok) { pushTok(tok, "plan"); });
    return out;
  }
  const fexpr = (cfg && cfg.formula_expr) || (it && it.formula_expr) || [];
  fexpr.forEach(function (tok) { pushTok(tok, "fact"); });
  ((cfg && cfg.sources) || (it && it.sources) || []).forEach(function (s) {
    if (!s) return;
    if (s.source_type === "spu_indicator" || s.source_type === "finance" || s.source_type === "manual") {
      if (s.source_indicator_id) out.push(spuDepNodeKey(s.source_indicator_id, "fact", parsed.woff));
    }
  });
  if (it && it.calc_left_id) out.push(spuDepNodeKey(it.calc_left_id, "fact", parsed.woff));
  if (it && it.calc_right_id) out.push(spuDepNodeKey(it.calc_right_id, "fact", parsed.woff));
  return out;
}

function makeWouldCycle(indicators, anchorDay) {
  const byId = {};
  (indicators || []).forEach(function (x) {
    if (x && x.indicator_id) byId[x.indicator_id] = x;
  });
  return function spuWouldCycle(indicatorId, nextCfg, side) {
    if (!indicatorId) return false;
    const mode = side === "plan" || side === "fact" || side === "both" ? side : "both";
    const resolveIt = function (id) {
      return byId[id] || (String(id) === String(indicatorId)
        ? {
          indicator_id: indicatorId,
          formula_expr: nextCfg && nextCfg.formula_expr,
          plan_formula_expr: nextCfg && nextCfg.plan_formula_expr,
          plan_calc_mode: nextCfg && nextCfg.plan_calc_mode,
          sources: nextCfg && nextCfg.sources
        }
        : null);
    };
    const resolveCfg = function (id, it, woff) {
      const off = Number(woff) || 0;
      if (String(id) === String(indicatorId) && nextCfg && off === 0) return nextCfg;
      let day = anchorDay || "";
      if (off && day) day = isoAddDays(day, off * 7);
      if (it && day) {
        const v = spuVersionAt(it, day);
        if (v) return spuConfigRecordFromVersion(it, v);
      }
      return {
        sources: (it && it.sources) || [],
        formula_expr: (it && it.formula_expr) || [],
        plan_formula_expr: (it && it.plan_formula_expr) || [],
        plan_calc_mode: (it && it.plan_calc_mode) || "manual"
      };
    };
    const color = {};
    function walk(node, depth) {
      if (!node) return false;
      if (color[node] === 1) return true;
      if (color[node] === 2) return false;
      if (depth > SPU_FORMULA_EVAL_MAX_DEPTH) return false;
      color[node] = 1;
      const next = spuDepEdgesFromNode(node, resolveIt, resolveCfg);
      for (let i = 0; i < next.length; i++) {
        if (walk(next[i], depth + 1)) return true;
      }
      color[node] = 2;
      return false;
    }
    const starts = [];
    if (mode === "plan" || mode === "both") starts.push(spuDepNodeKey(indicatorId, "plan", 0));
    if (mode === "fact" || mode === "both") starts.push(spuDepNodeKey(indicatorId, "fact", 0));
    for (let s = 0; s < starts.length; s++) {
      if (walk(starts[s], 0)) return true;
    }
    return false;
  };
}

function safeDiv(a, b) {
  if (b === 0) return null;
  const out = a / b;
  return Number.isFinite(out) ? out : null;
}

function evalPlanMargin(revPlan, rentPlan) {
  return revPlan * rentPlan / 100;
}

/* --- 1. mind_058-like: план маржи = 2541000 × 21 ÷ 100 = 533610; save plan OK --- */
const WEEK = "2026-11-19";
const REV = 2541000;
const RENT = 21;
const MARGIN_PLAN = evalPlanMargin(REV, RENT);
assert(MARGIN_PLAN === 533610, "control margin plan 533610");

const baseInds = [
  {
    indicator_id: "mind_058",
    name: "Маржинальная прибыль магазина",
    plan_calc_mode: "manual",
    plan_formula_expr: [],
    formula_expr: [],
    sources: []
  },
  {
    indicator_id: "mind_rent",
    name: "Рентабельность",
    plan_calc_mode: "manual",
    plan_formula_expr: [],
    formula_expr: [
      { t: "ind", id: "mind_058", value_kind: "fact", week_offset: 0 },
      { t: "op", v: "/" },
      { t: "ind", id: "mind_rev", value_kind: "fact", week_offset: 0 },
      { t: "op", v: "*" },
      { t: "num", v: 100 }
    ],
    sources: []
  },
  {
    indicator_id: "mind_rev",
    name: "Выручка",
    plan_calc_mode: "manual",
    plan_formula_expr: [],
    formula_expr: [],
    sources: []
  }
];

const wouldCycle = makeWouldCycle(baseInds, WEEK);
const marginPlanCfg = {
  sources: [],
  formula_expr: [],
  plan_calc_mode: "formula",
  plan_formula_expr: [
    { t: "ind", id: "mind_rev", value_kind: "plan", week_offset: 0 },
    { t: "op", v: "*" },
    { t: "ind", id: "mind_rent", value_kind: "plan", week_offset: 0 },
    { t: "op", v: "/" },
    { t: "num", v: 100 }
  ]
};
assert(wouldCycle("mind_058", marginPlanCfg, "plan") === false, "T1 margin plan save allowed");
assert(wouldCycle("mind_058", marginPlanCfg, "both") === false, "T1 both also allowed");

/* --- 2. plan A→plan B, fact B→fact A — допустимо --- */
assert(wouldCycle("mind_058", marginPlanCfg, "plan") === false, "T2 mixed plan/fact independent");

/* --- 3. реальный цикл plan→plan --- */
const planCycleInds = [
  {
    indicator_id: "pa",
    plan_calc_mode: "formula",
    plan_formula_expr: [{ t: "ind", id: "pb", value_kind: "plan", week_offset: 0 }],
    formula_expr: []
  },
  {
    indicator_id: "pb",
    plan_calc_mode: "formula",
    plan_formula_expr: [{ t: "ind", id: "pa", value_kind: "plan", week_offset: 0 }],
    formula_expr: []
  }
];
const wcPlan = makeWouldCycle(planCycleInds, WEEK);
assert(wcPlan("pa", {
  plan_calc_mode: "formula",
  plan_formula_expr: [{ t: "ind", id: "pb", value_kind: "plan", week_offset: 0 }],
  formula_expr: []
}, "plan") === true, "T3 plan↔plan forbidden");

/* --- 4. реальный цикл fact→fact --- */
const factCycleInds = [
  {
    indicator_id: "fa",
    plan_calc_mode: "manual",
    plan_formula_expr: [],
    formula_expr: [{ t: "ind", id: "fb", value_kind: "fact", week_offset: 0 }]
  },
  {
    indicator_id: "fb",
    plan_calc_mode: "manual",
    plan_formula_expr: [],
    formula_expr: [{ t: "ind", id: "fa", value_kind: "fact", week_offset: 0 }]
  }
];
const wcFact = makeWouldCycle(factCycleInds, WEEK);
assert(wcFact("fa", {
  plan_calc_mode: "manual",
  plan_formula_expr: [],
  formula_expr: [{ t: "ind", id: "fb", value_kind: "fact", week_offset: 0 }]
}, "fact") === true, "T4 fact↔fact forbidden");

/* --- 5. смешанный цикл plan→fact→plan --- */
const mixedCycleInds = [
  {
    indicator_id: "ma",
    plan_calc_mode: "formula",
    plan_formula_expr: [{ t: "ind", id: "mb", value_kind: "fact", week_offset: 0 }],
    formula_expr: []
  },
  {
    indicator_id: "mb",
    plan_calc_mode: "manual",
    plan_formula_expr: [],
    formula_expr: [{ t: "ind", id: "ma", value_kind: "plan", week_offset: 0 }]
  }
];
const wcMixed = makeWouldCycle(mixedCycleInds, WEEK);
assert(wcMixed("ma", {
  plan_calc_mode: "formula",
  plan_formula_expr: [{ t: "ind", id: "mb", value_kind: "fact", week_offset: 0 }],
  formula_expr: []
}, "plan") === true, "T5 mixed plan→fact→plan forbidden");

/* --- 6. допустимые смешанные зависимости --- */
const okMixedInds = [
  {
    indicator_id: "oa",
    plan_calc_mode: "formula",
    plan_formula_expr: [{ t: "ind", id: "ob", value_kind: "plan", week_offset: 0 }],
    formula_expr: []
  },
  {
    indicator_id: "ob",
    plan_calc_mode: "manual",
    plan_formula_expr: [],
    formula_expr: [{ t: "ind", id: "oa", value_kind: "fact", week_offset: 0 }]
  }
];
const wcOk = makeWouldCycle(okMixedInds, WEEK);
assert(wcOk("oa", {
  plan_calc_mode: "formula",
  plan_formula_expr: [{ t: "ind", id: "ob", value_kind: "plan", week_offset: 0 }],
  formula_expr: []
}, "both") === false, "T6 allowed mixed plan(A)→plan(B) + fact(B)→fact(A)");

/* --- 7. зависимость от предыдущей недели без цикла --- */
const prevInds = [
  {
    indicator_id: "pv",
    plan_calc_mode: "formula",
    plan_formula_expr: [{ t: "ind", id: "pv", value_kind: "plan", week_offset: -1 }],
    formula_expr: []
  }
];
const wcPrev = makeWouldCycle(prevInds, WEEK);
assert(wcPrev("pv", {
  plan_calc_mode: "formula",
  plan_formula_expr: [{ t: "ind", id: "pv", value_kind: "plan", week_offset: -1 }],
  formula_expr: []
}, "plan") === false, "T7 prev-week self ref not a cycle");

/* --- 8. разные версии формул по отчётным неделям ---
 * На пред. неделе vx↔vy (цикл в той версии).
 * На текущей vx = const → save vy / vx без цикла.
 * Save vz с операндом vx|plan|-1 входит в граф пред. недели → цикл. */
const verInds = [
  {
    indicator_id: "vx",
    plan_calc_mode: "formula",
    plan_formula_expr: [{ t: "num", v: 1 }],
    formula_expr: [],
    source_versions: [
      {
        version_id: "vx_old",
        effective_from: "2026-11-01",
        effective_to: "2026-11-18",
        plan_calc_mode: "formula",
        plan_formula_expr: [{ t: "ind", id: "vy", value_kind: "plan", week_offset: 0 }],
        formula_expr: []
      },
      {
        version_id: "vx_cur",
        effective_from: "2026-11-19",
        effective_to: "",
        plan_calc_mode: "formula",
        plan_formula_expr: [{ t: "num", v: 1 }],
        formula_expr: []
      }
    ]
  },
  {
    indicator_id: "vy",
    plan_calc_mode: "formula",
    plan_formula_expr: [{ t: "ind", id: "vx", value_kind: "plan", week_offset: 0 }],
    formula_expr: [],
    source_versions: [
      {
        version_id: "vy1",
        effective_from: "2026-11-01",
        effective_to: "",
        plan_calc_mode: "formula",
        plan_formula_expr: [{ t: "ind", id: "vx", value_kind: "plan", week_offset: 0 }],
        formula_expr: []
      }
    ]
  },
  {
    indicator_id: "vz",
    plan_calc_mode: "manual",
    plan_formula_expr: [],
    formula_expr: []
  }
];
const wcVer = makeWouldCycle(verInds, WEEK);
assert(wcVer("vx", {
  plan_calc_mode: "formula",
  plan_formula_expr: [{ t: "num", v: 1 }],
  formula_expr: []
}, "plan") === false, "T8 current vx const — no cycle");
assert(wcVer("vy", {
  plan_calc_mode: "formula",
  plan_formula_expr: [{ t: "ind", id: "vx", value_kind: "plan", week_offset: 0 }],
  formula_expr: []
}, "plan") === false, "T8 current vy→vx const — no cycle");
assert(wcVer("vz", {
  plan_calc_mode: "formula",
  plan_formula_expr: [{ t: "ind", id: "vx", value_kind: "plan", week_offset: -1 }],
  formula_expr: []
}, "plan") === true, "T8 prev-week version cycle via vz→vx|-1");

/* --- 9. список источников конструктора: рентабельность доступна для маржи --- */
function operandIndicators(list, excludeId) {
  return (list || []).filter(function (x) {
    if (!x || !x.indicator_id) return false;
    if (excludeId && x.indicator_id === excludeId) return false;
    return true;
  }).map(function (x) { return x.indicator_id; });
}
const ops = operandIndicators(baseInds, "mind_058");
assert(ops.indexOf("mind_rent") !== -1, "T9 rent available as operand");
assert(ops.indexOf("mind_rev") !== -1, "T9 rev available as operand");
assert(ops.indexOf("mind_058") === -1, "T9 self excluded");

/* --- 10. сохранение и повторное открытие карточки с формулой --- */
function applyManualPlanSave(it, draft) {
  const draftPlanFx = Array.isArray(draft.planFormula) ? draft.planFormula.slice() : [];
  if (draftPlanFx.length) it.plan_formula_expr = draftPlanFx;
  it.plan_calc_mode = draft.planCalcOn ? "formula" : "manual";
  it.formula_expr = [];
  return it;
}
function reopenCard(it) {
  return {
    plan_calc_mode: it.plan_calc_mode,
    plan_formula_expr: Array.isArray(it.plan_formula_expr) ? it.plan_formula_expr.slice() : []
  };
}
const card = {
  indicator_id: "mind_058",
  plan_calc_mode: "manual",
  plan_formula_expr: [],
  formula_expr: []
};
assert(wouldCycle(card.indicator_id, marginPlanCfg, "plan") === false, "T10 save gate");
applyManualPlanSave(card, { planCalcOn: true, planFormula: marginPlanCfg.plan_formula_expr });
const reopened = reopenCard(card);
assert(reopened.plan_calc_mode === "formula", "T10 reopen mode");
assert(reopened.plan_formula_expr.length === 5, "T10 reopen formula tokens");
assert(reopened.plan_formula_expr[0].id === "mind_rev", "T10 reopen first operand");

/* --- old buggy union graph would false-positive on T1 --- */
function legacyWouldCycleBareIds(indicators, indicatorId, nextCfg) {
  function depIds(it, cfg) {
    const ids = [];
    const add = function (id) { if (id && ids.indexOf(id) === -1) ids.push(id); };
    ((cfg && cfg.formula_expr) || (it && it.formula_expr) || []).forEach(function (tok) {
      if (tok && tok.t === "ind") add(tok.id);
    });
    ((cfg && cfg.plan_formula_expr) || (it && it.plan_formula_expr) || []).forEach(function (tok) {
      if (tok && tok.t === "ind") add(tok.id);
    });
    return ids;
  }
  const map = {};
  indicators.forEach(function (it) {
    map[it.indicator_id] = depIds(it, it);
  });
  map[indicatorId] = depIds(null, nextCfg);
  const seen = {};
  function walk(id) {
    if (!id) return false;
    if (seen[id]) return true;
    seen[id] = true;
    const next = map[id] || [];
    for (let i = 0; i < next.length; i++) {
      if (walk(next[i])) return true;
    }
    return false;
  }
  return walk(indicatorId);
}
assert(legacyWouldCycleBareIds(baseInds, "mind_058", marginPlanCfg) === true, "legacy false positive on margin");
assert(safeDiv(MARGIN_PLAN, REV) * 100 === RENT, "rent from margin/rev identity");

console.log("wt_spu_plan_fact_cycle_harness: OK");
console.log("control margin plan=", MARGIN_PLAN);
