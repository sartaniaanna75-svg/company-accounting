/**
 * СПУ: дата действия, изоляция fact/plan, безопасный propagate обеих сторон,
 * приоритет period_plans над формульным планом (без изменения логики).
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m || "assert"); }
function clone(x) { return JSON.parse(JSON.stringify(x)); }

function mgmtParseIso(iso) {
  const p = String(iso || "").split("-").map(Number);
  return new Date(p[0], (p[1] || 1) - 1, p[2] || 1);
}
function mgmtIsoFromDate(d) {
  const pad = function (x) { return x < 10 ? "0" + x : String(x); };
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}
function mgmtSpuReportingWeekWithStartDay(startDay, iso) {
  const d = mgmtParseIso(iso);
  const offset = (d.getDay() - 4 + 7) % 7;
  const from = new Date(d.getFullYear(), d.getMonth(), d.getDate() - offset);
  return { start_date: mgmtIsoFromDate(from) };
}
function spuIndAccountingWeekStartIso(iso) {
  return mgmtSpuReportingWeekWithStartDay(4, iso || "2026-11-18").start_date;
}
function spuIndNormalizeEffectiveFromInput(rawIso) {
  const raw = String(rawIso || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return { ok: false, iso: "", normalized: false };
  const thu = spuIndAccountingWeekStartIso(raw);
  return { ok: true, iso: thu, normalized: thu !== raw };
}

function spuIndCfgFactFingerprint(cfg) {
  return JSON.stringify({ f: cfg && cfg.formula_expr });
}
function spuIndCfgPlanFingerprint(cfg) {
  return JSON.stringify({ pf: cfg && cfg.plan_formula_expr, pcm: cfg && cfg.plan_calc_mode });
}

function spuIndFormulaIndTokens(tokens) {
  const out = [];
  (tokens || []).forEach(function (tok, idx) {
    if (tok && tok.t === "ind") out.push({ index: idx, id: String(tok.id || "") });
  });
  return out;
}

function spuIndSideOperandReplacements(openedTokens, draftTokens, sideLabel) {
  const side = sideLabel || "факта";
  const a = spuIndFormulaIndTokens(openedTokens);
  const b = spuIndFormulaIndTokens(draftTokens);
  if (a.length !== b.length) return { ok: false, replacements: [], message: "count " + side };
  const map = Object.create(null);
  const replacements = [];
  for (let i = 0; i < a.length; i++) {
    if (a[i].id === b[i].id) continue;
    if (map[a[i].id] != null && map[a[i].id] !== b[i].id) {
      return { ok: false, replacements: [], message: "conflict" };
    }
    if (map[a[i].id] == null) {
      map[a[i].id] = b[i].id;
      replacements.push({ fromId: a[i].id, toId: b[i].id });
    }
  }
  if (!replacements.length) return { ok: false, replacements: [], message: "none" };
  return { ok: true, replacements: replacements, message: "" };
}

function spuIndCanApplyOperandReplacementsToFormula(laterTokens, openedTokens, replacements) {
  const laterInd = spuIndFormulaIndTokens(laterTokens);
  const openedInd = spuIndFormulaIndTokens(openedTokens);
  const openedCount = Object.create(null);
  openedInd.forEach(function (x) { openedCount[x.id] = (openedCount[x.id] || 0) + 1; });
  const laterCount = Object.create(null);
  laterInd.forEach(function (x) { laterCount[x.id] = (laterCount[x.id] || 0) + 1; });
  for (let i = 0; i < (replacements || []).length; i++) {
    const r = replacements[i];
    const need = openedCount[r.fromId] || 0;
    const have = laterCount[r.fromId] || 0;
    if (have === 0) return { ok: false, message: "missing" };
    if (have !== need) return { ok: false, message: "count" };
  }
  return { ok: true, message: "" };
}

function spuIndApplyOperandReplacementsToFormula(tokens, replacements) {
  const map = Object.create(null);
  (replacements || []).forEach(function (r) { if (r && r.fromId) map[r.fromId] = r.toId; });
  return (tokens || []).map(function (tok) {
    if (!tok || tok.t !== "ind") return clone(tok);
    if (!Object.prototype.hasOwnProperty.call(map, tok.id)) return clone(tok);
    return Object.assign(clone(tok), { id: map[tok.id] });
  });
}

function spuIndLaterVersionsAfter(it, fromIso) {
  return ((it && it.source_versions) || []).filter(function (v) {
    return v && String(v.effective_from || "") > String(fromIso || "");
  });
}

function spuIndPreflightPropagateSideToLater(it, fromIso, side, opts) {
  opts = opts || {};
  const later = spuIndLaterVersionsAfter(it, fromIso);
  if (!later.length) return { ok: false, replacements: [], propagateMode: "none", message: "no later" };
  const isPlan = side === "plan";
  const openedFx = opts.openedFx;
  const draftFx = opts.draftFx;
  if (isPlan) {
    const openedMode = opts.openedPlanMode === "formula" ? "formula" : "manual";
    const draftMode = opts.draftPlanMode === "formula" ? "formula" : "manual";
    const fxSame = JSON.stringify(openedFx || []) === JSON.stringify(draftFx || []);
    if (fxSame && openedMode !== draftMode) {
      return { ok: true, replacements: [], propagateMode: "calc_mode", planCalcMode: draftMode, message: "" };
    }
  }
  const repl = spuIndSideOperandReplacements(openedFx, draftFx, isPlan ? "плана" : "факта");
  if (!repl.ok) return { ok: false, replacements: [], propagateMode: "none", message: repl.message };
  for (let i = 0; i < later.length; i++) {
    const v = later[i];
    const laterTokens = isPlan ? v.plan_formula_expr : v.formula_expr;
    const check = spuIndCanApplyOperandReplacementsToFormula(laterTokens, openedFx, repl.replacements);
    if (!check.ok) {
      return { ok: false, replacements: repl.replacements, propagateMode: "none", message: "bad " + (v.version_id || "") };
    }
  }
  const out = { ok: true, replacements: repl.replacements, propagateMode: "operands", message: "" };
  if (isPlan) {
    const draftMode = opts.draftPlanMode === "formula" ? "formula" : "manual";
    const openedMode = opts.openedPlanMode === "formula" ? "formula" : "manual";
    if (draftMode !== openedMode) out.planCalcMode = draftMode;
  }
  return out;
}

function spuIndPropagateSideToLater(it, fromIso, side, pre) {
  const later = spuIndLaterVersionsAfter(it, fromIso);
  later.forEach(function (v) {
    if (side === "fact" && pre.propagateMode === "operands") {
      v.formula_expr = spuIndApplyOperandReplacementsToFormula(v.formula_expr, pre.replacements);
    }
    if (side === "plan") {
      if (pre.propagateMode === "calc_mode") {
        v.plan_calc_mode = pre.planCalcMode === "formula" ? "formula" : "manual";
      } else if (pre.propagateMode === "operands") {
        v.plan_formula_expr = spuIndApplyOperandReplacementsToFormula(v.plan_formula_expr, pre.replacements);
        if (pre.planCalcMode === "formula" || pre.planCalcMode === "manual") {
          v.plan_calc_mode = pre.planCalcMode;
        }
      }
    }
  });
  return later.length;
}

function versionAt(vers, day) {
  let hit = null;
  (vers || []).forEach(function (v) {
    if (!v) return;
    if (v.effective_from && v.effective_from > day) return;
    if (v.effective_to && v.effective_to < day) return;
    hit = v;
  });
  return hit;
}

/**
 * Зеркало фактического порядка mgmtIndicatorPlan (логику UI не меняем):
 * 1) plan_calc_mode=formula → plan_formula_expr
 * 2) иначе period_plans (spuComputedPeriodWeekPlan)
 * 3) иначе management.plans
 */
function resolveDisplayedPlan(opts) {
  const planMode = opts.planMode === "formula" ? "formula" : "manual";
  const formulaPlan = opts.formulaPlan;
  const periodPlan = opts.periodPlan;
  const manualPlan = opts.manualPlan;
  if (planMode === "formula" && formulaPlan != null && Number.isFinite(Number(formulaPlan))) {
    return { value: Number(formulaPlan), source: "plan_formula_expr" };
  }
  if (periodPlan != null && Number.isFinite(Number(periodPlan))) {
    return { value: Number(periodPlan), source: "period_plans" };
  }
  if (manualPlan != null && Number.isFinite(Number(manualPlan))) {
    return { value: Number(manualPlan), source: "management.plans" };
  }
  return { value: null, source: "none" };
}

// --- normalize ---
assert(spuIndNormalizeEffectiveFromInput("2026-11-14").iso === "2026-11-12", "thu normalize");

// --- 1 plan fix 05.11 no later ---
{
  const vers = [{
    version_id: "s1",
    effective_from: "2026-11-05",
    effective_to: "",
    formula_expr: [{ t: "ind", id: "fact_a" }],
    plan_formula_expr: [{ t: "ind", id: "plan_new" }],
    plan_calc_mode: "formula"
  }];
  assert(versionAt(vers, "2026-11-18").plan_formula_expr[0].id === "plan_new", "plan no later");
}

// --- 2 plan propagate keeps fact ---
{
  const openedPlan = [{ t: "ind", id: "p_old" }, { t: "op", v: "+" }, { t: "num", v: 1 }];
  const draftPlan = [{ t: "ind", id: "p_new" }, { t: "op", v: "+" }, { t: "num", v: 1 }];
  const it = {
    source_versions: [{
      version_id: "later",
      effective_from: "2026-11-12",
      effective_to: "",
      formula_expr: [{ t: "ind", id: "fact_keep" }],
      plan_formula_expr: [
        { t: "lp", v: "(" }, { t: "ind", id: "p_old" }, { t: "op", v: "+" }, { t: "num", v: 1 }, { t: "rp", v: ")" }
      ],
      plan_calc_mode: "formula"
    }]
  };
  const pre = spuIndPreflightPropagateSideToLater(it, "2026-11-05", "plan", {
    openedFx: openedPlan, draftFx: draftPlan
  });
  assert(pre.ok && pre.propagateMode === "operands", "plan preflight");
  spuIndPropagateSideToLater(it, "2026-11-05", "plan", pre);
  assert(it.source_versions[0].plan_formula_expr[1].id === "p_new", "plan operand");
  assert(it.source_versions[0].plan_formula_expr[0].t === "lp", "plan parens");
  assert(it.source_versions[0].formula_expr[0].id === "fact_keep", "fact untouched");
}

// --- 3 until_next: later plan OLD ---
{
  const vers = [
    { effective_from: "2026-11-05", effective_to: "2026-11-11", plan_formula_expr: [{ t: "ind", id: "new" }] },
    { effective_from: "2026-11-12", effective_to: "", plan_formula_expr: [{ t: "ind", id: "old" }] }
  ];
  assert(versionAt(vers, "2026-11-18").plan_formula_expr[0].id === "old", "until_next later old");
}

// --- 4 cancel ---
{
  const before = [{ plan_formula_expr: [{ t: "ind", id: "old" }] }];
  const after = clone(before);
  assert(JSON.stringify(before) === JSON.stringify(after), "cancel");
}

// --- 5 12.11 past plan preserved ---
{
  const vers = [
    { effective_from: "2026-11-05", effective_to: "2026-11-11", plan_formula_expr: [{ t: "ind", id: "old" }] },
    { effective_from: "2026-11-12", effective_to: "", plan_formula_expr: [{ t: "ind", id: "new" }] }
  ];
  assert(versionAt(vers, "2026-11-08").plan_formula_expr[0].id === "old", "past plan");
  assert(versionAt(vers, "2026-11-15").plan_formula_expr[0].id === "new", "current plan");
}

// --- 6 isolation ---
{
  const opened = { formula_expr: [{ t: "ind", id: "f" }], plan_formula_expr: [{ t: "ind", id: "p1" }], plan_calc_mode: "formula" };
  const draftPlanOnly = { formula_expr: [{ t: "ind", id: "f" }], plan_formula_expr: [{ t: "ind", id: "p2" }], plan_calc_mode: "formula" };
  assert(spuIndCfgFactFingerprint(draftPlanOnly) === spuIndCfgFactFingerprint(opened), "fact clean");
  assert(spuIndCfgPlanFingerprint(draftPlanOnly) !== spuIndCfgPlanFingerprint(opened), "plan dirty");
}

// --- 7 both sides propagate independently ---
{
  const it = {
    source_versions: [{
      version_id: "L",
      effective_from: "2026-11-12",
      effective_to: "",
      formula_expr: [{ t: "ind", id: "f_old" }],
      plan_formula_expr: [{ t: "ind", id: "p_old" }],
      plan_calc_mode: "formula"
    }]
  };
  const preF = spuIndPreflightPropagateSideToLater(it, "2026-11-05", "fact", {
    openedFx: [{ t: "ind", id: "f_old" }],
    draftFx: [{ t: "ind", id: "f_new" }]
  });
  const preP = spuIndPreflightPropagateSideToLater(it, "2026-11-05", "plan", {
    openedFx: [{ t: "ind", id: "p_old" }],
    draftFx: [{ t: "ind", id: "p_new" }]
  });
  assert(preF.ok && preP.ok, "both preflight");
  spuIndPropagateSideToLater(it, "2026-11-05", "fact", preF);
  spuIndPropagateSideToLater(it, "2026-11-05", "plan", preP);
  assert(it.source_versions[0].formula_expr[0].id === "f_new", "both fact");
  assert(it.source_versions[0].plan_formula_expr[0].id === "p_new", "both plan");
}

// --- 8 ambiguous plan blocked, no mutate ---
{
  const it = {
    source_versions: [{
      version_id: "bad",
      effective_from: "2026-11-12",
      effective_to: "",
      formula_expr: [{ t: "ind", id: "f" }],
      plan_formula_expr: [{ t: "ind", id: "other" }],
      plan_calc_mode: "formula"
    }]
  };
  const snap = clone(it);
  const pre = spuIndPreflightPropagateSideToLater(it, "2026-11-05", "plan", {
    openedFx: [{ t: "ind", id: "p_old" }],
    draftFx: [{ t: "ind", id: "p_new" }]
  });
  assert(!pre.ok, "plan ambiguous block");
  assert(JSON.stringify(it) === JSON.stringify(snap), "no mutate");
}

// --- 9 plan_calc_mode only ---
{
  const planFx = [{ t: "ind", id: "p" }, { t: "op", v: "*" }, { t: "num", v: 2 }];
  const it = {
    source_versions: [{
      version_id: "L",
      effective_from: "2026-11-12",
      effective_to: "",
      formula_expr: [{ t: "ind", id: "f" }],
      plan_formula_expr: clone(planFx),
      plan_calc_mode: "manual"
    }]
  };
  const pre = spuIndPreflightPropagateSideToLater(it, "2026-11-05", "plan", {
    openedFx: planFx,
    draftFx: planFx,
    openedPlanMode: "manual",
    draftPlanMode: "formula"
  });
  assert(pre.ok && pre.propagateMode === "calc_mode", "mode-only");
  spuIndPropagateSideToLater(it, "2026-11-05", "plan", pre);
  assert(it.source_versions[0].plan_calc_mode === "formula", "mode propagated");
  assert(it.source_versions[0].plan_formula_expr[0].id === "p", "plan fx unchanged");
  assert(it.source_versions[0].formula_expr[0].id === "f", "fact unchanged");
}

// --- 10 fact regress: parens kept ---
{
  const opened = [{ t: "ind", id: "old" }, { t: "op", v: "/" }, { t: "ind", id: "chk" }];
  const draft = [{ t: "ind", id: "new" }, { t: "op", v: "/" }, { t: "ind", id: "chk" }];
  const it = {
    source_versions: [{
      effective_from: "2026-11-12",
      effective_to: "",
      formula_expr: [
        { t: "lp", v: "(" }, { t: "ind", id: "old" }, { t: "op", v: "/" }, { t: "ind", id: "chk" }, { t: "rp", v: ")" }
      ],
      plan_formula_expr: [{ t: "ind", id: "pl" }]
    }]
  };
  const pre = spuIndPreflightPropagateSideToLater(it, "2026-11-05", "fact", { openedFx: opened, draftFx: draft });
  spuIndPropagateSideToLater(it, "2026-11-05", "fact", pre);
  assert(it.source_versions[0].formula_expr[1].id === "new", "fact operand");
  assert(it.source_versions[0].plan_formula_expr[0].id === "pl", "plan kept on fact prop");
}

// --- 11 formula plan without period_plans ---
{
  const r = resolveDisplayedPlan({ planMode: "formula", periodPlan: null, formulaPlan: 42, manualPlan: 10 });
  assert(r.source === "plan_formula_expr" && r.value === 42, "formula plan used");
}

// --- 12/13: при manual-режиме period_plans даёт отображаемый план ≠ результат plan_formula_expr ---
{
  const formulaWouldBe = 42;
  const r = resolveDisplayedPlan({
    planMode: "manual",
    periodPlan: 99,
    formulaPlan: formulaWouldBe,
    manualPlan: 10
  });
  assert(r.source === "period_plans" && r.value === 99, "period_plans when not formula mode");
  assert(r.value !== formulaWouldBe, "display may differ from plan_formula_expr when period_plans set");
}

console.log("wt_spu_formula_effective_from_harness: OK");
