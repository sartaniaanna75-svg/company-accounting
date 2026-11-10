/**
 * calculated + spu_layer=totals: NeedsSetup by formula completeness, not hard true.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

function spuIndicatorLayer(it) {
  const v = String((it && it.spu_layer) || "");
  return (v === "totals" || v === "key") ? v : "";
}

function spuIndicatorUiKind(it) {
  if (!it) return "manual";
  if (it.indicator_kind === "calculated" || it.source_type === "calculated"
      || it.aggregation_type === "formula" || it.calc_mode === "formula") return "calculated";
  if (it.source_type === "one_c_future") return "one_c";
  if (it.source_type === "worktime_metric" || it.source_type === "timesheet") return "worktime";
  return "manual";
}

function spuActiveConfig(it) {
  return { formula_expr: (it && it._activeFx) != null ? it._activeFx : undefined, sources: [] };
}

function spuIndicatorFormulaNeedsSetup(it) {
  const cfg = spuActiveConfig(it);
  let fx = (cfg && Array.isArray(cfg.formula_expr) && cfg.formula_expr.length) ? cfg.formula_expr : null;
  if (!fx) fx = (it && Array.isArray(it.formula_expr)) ? it.formula_expr : [];
  if (!fx.length) return true;
  return fx.some(function (t) { return t && t.t === "ind" && !t.id; });
}

function spuIndicatorNeedsSetup(it) {
  if (!it || it.is_deleted) return false;
  const layer = spuIndicatorLayer(it);
  if (!layer) return true;
  const kind = spuIndicatorUiKind(it);
  const isCalc = kind === "calculated" || it.aggregation_type === "formula" || it.calc_mode === "formula";
  if (layer === "totals") {
    if (kind === "manual" || kind === "one_c") return false;
    if (isCalc) return spuIndicatorFormulaNeedsSetup(it);
    return true;
  }
  if (layer === "key") {
    if (isCalc) return spuIndicatorFormulaNeedsSetup(it);
    if (kind === "manual" || kind === "one_c") return false;
    return true;
  }
  return true;
}

function spuSettingsBucket(it) {
  if (!spuIndicatorLayer(it) || spuIndicatorNeedsSetup(it)) return "unset";
  return spuIndicatorLayer(it);
}

const mind068 = {
  indicator_id: "mind_068",
  spu_layer: "totals",
  indicator_kind: "calculated",
  source_type: "calculated",
  aggregation_type: "formula",
  formula_expr: [
    { t: "ind", id: "mind_a" },
    { t: "op", v: "-" },
    { t: "ind", id: "mind_b" }
  ]
};

assert(spuIndicatorNeedsSetup(mind068) === false, "valid calc totals configured");
assert(spuSettingsBucket(mind068) === "totals", "bucket totals not unset");

const emptyFx = Object.assign({}, mind068, { formula_expr: [] });
assert(spuIndicatorNeedsSetup(emptyFx) === true, "empty formula still needs setup");
assert(spuSettingsBucket(emptyFx) === "unset", "empty stays unset");

const incomplete = Object.assign({}, mind068, {
  formula_expr: [{ t: "ind", id: "" }, { t: "op", v: "+" }, { t: "ind", id: "mind_b" }]
});
assert(spuIndicatorNeedsSetup(incomplete) === true, "blank ind needs setup");

// empty active [] must not block root formula_expr
const quirk = Object.assign({}, mind068, { _activeFx: [] });
assert(spuIndicatorFormulaNeedsSetup(quirk) === false, "empty active fx falls back to root");

const keyOk = Object.assign({}, mind068, { spu_layer: "key" });
assert(spuSettingsBucket(keyOk) === "key", "key calc still works");

console.log("OK wt_spu_needs_setup_totals_calc_harness");
