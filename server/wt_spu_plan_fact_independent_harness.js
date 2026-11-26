/**
 * Stage 3: plan_calc_mode / plan_formula_expr independent of fact kind.
 * Mirrors save rules (no wipe on manual/one_c; keep expr when plan off).
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m || "assert"); }

function applyPlanSave(it, kind, draft) {
  const planCalcOn = !!draft.planCalcOn;
  const draftPlanFx = Array.isArray(draft.planFormula) ? draft.planFormula.slice() : [];
  const allowPlanFormula = (kind === "calculated" || kind === "worktime"
    || kind === "manual" || kind === "one_c");
  if (allowPlanFormula) {
    if (draftPlanFx.length) it.plan_formula_expr = draftPlanFx;
    else if (!Array.isArray(it.plan_formula_expr)) it.plan_formula_expr = [];
    it.plan_calc_mode = planCalcOn ? "formula" : "manual";
  } else {
    it.plan_formula_expr = [];
    it.plan_calc_mode = "manual";
  }
  return it;
}

/** Old buggy manual save (pre stage 3) — wiped plan_*. */
function applyManualSaveLegacyWipe(it) {
  it.formula_expr = [];
  it.plan_formula_expr = [];
  it.plan_calc_mode = "manual";
  return it;
}

function applyManualFactSide(it, kind) {
  if (kind === "one_c") {
    it.source_type = "one_c_future";
    it.indicator_kind = "one_c";
  } else if (kind === "manual") {
    it.source_type = "manual";
    it.indicator_kind = "manual";
  } else if (kind === "worktime") {
    it.source_type = "worktime_metric";
    it.indicator_kind = "worktime";
  } else if (kind === "calculated") {
    it.source_type = "calculated";
    it.indicator_kind = "calculated";
  }
  if (kind === "manual" || kind === "one_c") {
    it.sources = [];
    it.formula_expr = [];
    it.aggregation_type = "none";
    it.calc_mode = "none";
  }
  return it;
}

function mgmtIndicatorPlan(it, manuals) {
  if (!it) return null;
  if (it.plan_calc_mode === "formula" && Array.isArray(it.plan_formula_expr) && it.plan_formula_expr.length) {
    const toks = it.plan_formula_expr;
    function val(tok) {
      if (!tok) return null;
      if (tok.t === "num") return Number(tok.v);
      if (tok.t === "ind") {
        const m = manuals && manuals[tok.id];
        return m == null ? null : Number(m);
      }
      return null;
    }
    // revenue * rent / 100
    if (toks.length >= 5 && toks[1].v === "*" && toks[3].v === "/") {
      const a = val(toks[0]);
      const b = val(toks[2]);
      const c = val(toks[4]);
      if (a == null || b == null || c == null || c === 0) return null;
      return a * b / c;
    }
    if (toks.length === 1) return val(toks[0]);
    return null;
  }
  if (it._storedPlan != null) return it._storedPlan;
  return null;
}

const marginFx = [
  { t: "ind", id: "rev", value_kind: "plan" },
  { t: "op", v: "*" },
  { t: "ind", id: "rent", value_kind: "plan" },
  { t: "op", v: "/" },
  { t: "num", v: 100 }
];
const manuals = { rev: 2541000, rent: 21 };

function matrixCase(kind, planOn, label) {
  const it = {
    indicator_id: "mind_m",
    name: "Маржа",
    _storedPlan: 10,
    _manualFact: 999,
    plan_formula_expr: [],
    plan_calc_mode: "manual"
  };
  applyManualFactSide(it, kind);
  applyPlanSave(it, kind, { planCalcOn: planOn, planFormula: planOn ? marginFx : marginFx });
  if (planOn) {
    assert(it.plan_calc_mode === "formula", label + " mode formula");
    assert(it.plan_formula_expr.length === 5, label + " expr");
    const p = mgmtIndicatorPlan(it, manuals);
    assert(p === 533610, label + " plan got " + p);
  } else {
    assert(it.plan_calc_mode === "manual", label + " mode manual");
    assert(it.plan_formula_expr.length === 5, label + " expr kept when off");
    assert(mgmtIndicatorPlan(it, manuals) === 10, label + " stored plan");
  }
  assert(it._manualFact === 999, label + " fact untouched");
  if (kind === "manual") assert(it.source_type === "manual", label + " fact type");
  if (kind === "one_c") assert(it.source_type === "one_c_future", label + " one_c type");
  if (kind === "worktime") assert(it.source_type === "worktime_metric", label + " wt type");
  if (kind === "calculated") assert(it.source_type === "calculated", label + " calc type");
}

/* 1–8 matrix */
["manual", "worktime", "calculated", "one_c"].forEach(function (kind) {
  matrixCase(kind, false, kind + "+manualPlan");
  matrixCase(kind, true, kind + "+formulaPlan");
});

/* 9 reopen */
{
  const saved = {
    source_type: "manual",
    indicator_kind: "manual",
    plan_calc_mode: "formula",
    plan_formula_expr: marginFx.slice(),
    _manualFact: 42
  };
  const draft = JSON.parse(JSON.stringify(saved));
  assert(draft.plan_calc_mode === "formula", "reopen mode");
  assert(mgmtIndicatorPlan(draft, manuals) === 533610, "reopen plan");
  assert(draft._manualFact === 42, "reopen fact");
}

/* 10 change fact kind keeps plan */
{
  const it = {
    source_type: "worktime_metric",
    indicator_kind: "worktime",
    plan_calc_mode: "formula",
    plan_formula_expr: marginFx.slice(),
    formula_expr: [{ t: "num", v: 1 }],
    _manualFact: 7
  };
  applyManualFactSide(it, "manual");
  applyPlanSave(it, "manual", { planCalcOn: true, planFormula: it.plan_formula_expr });
  assert(it.source_type === "manual", "kind→manual fact");
  assert(it.formula_expr.length === 0, "fact formula cleared for manual");
  assert(it.plan_calc_mode === "formula", "plan mode kept");
  assert(mgmtIndicatorPlan(it, manuals) === 533610, "plan still works");
}

/* 11 off keeps expr + stored plans */
{
  const it = {
    source_type: "manual",
    plan_calc_mode: "formula",
    plan_formula_expr: marginFx.slice(),
    _storedPlan: 100
  };
  applyPlanSave(it, "manual", { planCalcOn: false, planFormula: marginFx });
  assert(it.plan_calc_mode === "manual", "off");
  assert(it.plan_formula_expr.length === 5, "expr kept");
  assert(mgmtIndicatorPlan(it, manuals) === 100, "stored used");
}

/* 12 re-enable */
{
  const it = {
    source_type: "manual",
    plan_calc_mode: "manual",
    plan_formula_expr: marginFx.slice(),
    _storedPlan: 100
  };
  applyPlanSave(it, "manual", { planCalcOn: true, planFormula: marginFx });
  assert(mgmtIndicatorPlan(it, manuals) === 533610, "re-on");
}

/* 13 save without changing plan formula */
{
  const it = {
    source_type: "manual",
    plan_calc_mode: "formula",
    plan_formula_expr: marginFx.slice()
  };
  applyPlanSave(it, "manual", { planCalcOn: true, planFormula: marginFx });
  assert(JSON.stringify(it.plan_formula_expr) === JSON.stringify(marginFx), "unchanged expr");
}

/* legacy wipe would destroy — ensure new path does not */
{
  const it = {
    source_type: "manual",
    plan_calc_mode: "formula",
    plan_formula_expr: marginFx.slice()
  };
  applyManualSaveLegacyWipe(Object.assign({}, it));
  applyPlanSave(it, "manual", { planCalcOn: true, planFormula: marginFx });
  assert(it.plan_calc_mode === "formula", "no wipe");
  assert(mgmtIndicatorPlan(it, manuals) === 533610, "control 533610");
}

console.log("OK wt_spu_plan_fact_independent_harness");
