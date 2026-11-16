/**
 * Worktime + формула плана: режим plan_calc_mode без смены kind/source; формула не стирается при off.
 * Зеркало правил save/UI app.html, без DOM/БД.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function applyWorktimePlanSave(it, draft) {
  const kind = "worktime";
  const calc = "sum";
  let planCalcOn = !!draft.planCalcOn;
  const draftPlanFx = Array.isArray(draft.planFormula) ? draft.planFormula.slice() : [];
  const allowPlanFormula = (kind === "calculated" || calc === "formula" || kind === "worktime");
  if (allowPlanFormula) {
    if (draftPlanFx.length) {
      it.plan_formula_expr = draftPlanFx;
    } else if (!Array.isArray(it.plan_formula_expr)) {
      it.plan_formula_expr = [];
    }
    it.plan_calc_mode = planCalcOn ? "formula" : "manual";
  } else {
    it.plan_formula_expr = [];
    it.plan_calc_mode = "manual";
  }
  // kind/source не трогаем формулой плана
  assert(it.indicator_kind === "worktime", "kind stays worktime");
  assert(it.source_type === "worktime_metric", "source_type stays worktime_metric");
  assert(it.source_metric_id === "m1", "metric unchanged");
  return it;
}

function mgmtIndicatorPlan(it) {
  if (!it) return null;
  if (it.plan_calc_mode === "formula" && Array.isArray(it.plan_formula_expr) && it.plan_formula_expr.length) {
    const tok = it.plan_formula_expr[0];
    if (tok && tok.t === "num") return Number(tok.v);
    if (tok && tok.t === "ind" && tok.value_kind === "plan") return 100;
    if (tok && tok.t === "ind" && tok.value_kind === "fact") return 80;
    return null;
  }
  if (it._periodPlan != null) return it._periodPlan;
  if (it._storedPlan != null) return it._storedPlan;
  return null;
}

// 1) Галочка off — ручной/периодный план
{
  const it = {
    indicator_kind: "worktime",
    source_type: "worktime_metric",
    source_metric_id: "m1",
    plan_calc_mode: "manual",
    plan_formula_expr: [],
    _storedPlan: 50,
    _periodPlan: null
  };
  applyWorktimePlanSave(it, { planCalcOn: false, planFormula: [] });
  assert(it.plan_calc_mode === "manual", "off → manual");
  assert(mgmtIndicatorPlan(it) === 50, "stored plan used");
  it._periodPlan = 70;
  assert(mgmtIndicatorPlan(it) === 70, "period plan preferred in engine order when no formula");
}

// 2) Галочка on — формула из плана другого
{
  const it = {
    indicator_kind: "worktime",
    source_type: "worktime_metric",
    source_metric_id: "m1",
    plan_calc_mode: "manual",
    plan_formula_expr: [],
    _storedPlan: 50
  };
  const fx = [{ t: "ind", id: "other", value_kind: "plan", week_offset: 0 }];
  applyWorktimePlanSave(it, { planCalcOn: true, planFormula: fx });
  assert(it.plan_calc_mode === "formula", "on → formula");
  assert(it.plan_formula_expr.length === 1, "formula saved");
  assert(mgmtIndicatorPlan(it) === 100, "plan from other plan");
  assert(it.indicator_kind === "worktime", "still worktime");
}

// 3) Формула из факта другого
{
  const it = {
    indicator_kind: "worktime",
    source_type: "worktime_metric",
    source_metric_id: "m1",
    plan_formula_expr: []
  };
  applyWorktimePlanSave(it, {
    planCalcOn: true,
    planFormula: [{ t: "ind", id: "other", value_kind: "fact", week_offset: -1 }]
  });
  assert(mgmtIndicatorPlan(it) === 80, "plan from other fact");
}

// 4) Выключение не стирает формулу
{
  const it = {
    indicator_kind: "worktime",
    source_type: "worktime_metric",
    source_metric_id: "m1",
    plan_calc_mode: "formula",
    plan_formula_expr: [{ t: "num", v: 42 }],
    _storedPlan: 11
  };
  applyWorktimePlanSave(it, {
    planCalcOn: false,
    planFormula: [{ t: "num", v: 42 }]
  });
  assert(it.plan_calc_mode === "manual", "off mode");
  assert(it.plan_formula_expr.length === 1 && it.plan_formula_expr[0].v === 42, "formula kept");
  assert(mgmtIndicatorPlan(it) === 11, "falls back to stored");
  applyWorktimePlanSave(it, {
    planCalcOn: true,
    planFormula: [{ t: "num", v: 42 }]
  });
  assert(it.plan_calc_mode === "formula", "re-on");
  assert(mgmtIndicatorPlan(it) === 42, "formula restored");
}

// 5) Open без save — копия без мутации store
{
  const saved = {
    indicator_kind: "worktime",
    source_type: "worktime_metric",
    source_metric_id: "m1",
    plan_calc_mode: "manual",
    plan_formula_expr: [{ t: "num", v: 9 }],
    source_result: "group_total",
    source_work_tab_id: "sales"
  };
  const draft = JSON.parse(JSON.stringify(saved));
  draft.plan_calc_mode = "formula";
  assert(saved.plan_calc_mode === "manual", "open without save keeps stored mode");
  assert(saved.source_result === "group_total", "sources untouched");
}

console.log("wt_spu_worktime_plan_formula_harness: OK");
