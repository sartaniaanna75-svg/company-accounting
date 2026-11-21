/**
 * WorkTime source save/reopen: multi-version insert_segment + propagate.
 * Зеркало логики app.html без DOM. Без БД.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function isoAddDays(iso, n) {
  const p = String(iso || "").split("-").map(Number);
  const d = new Date(p[0], (p[1] || 1) - 1, p[2] || 1);
  d.setDate(d.getDate() + Number(n || 0));
  const pad = function (x) { return (x < 10 ? "0" : "") + x; };
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}

function spuNewSourceRow(s) {
  return Object.assign({
    source_type: "worktime_metric",
    source_metric_id: "",
    source_result: "metric_overall"
  }, s || {});
}

function spuVersionCoversDay(v, day) {
  if (!v) return false;
  if (v.effective_from && String(v.effective_from) > day) return false;
  if (v.effective_to && String(v.effective_to) < day) return false;
  return true;
}

function spuVersionAt(it, iso) {
  let hit = null;
  (it.source_versions || []).forEach(function (v) {
    if (!v) return;
    if (v.effective_from && String(v.effective_from) > iso) return;
    if (v.effective_to && String(v.effective_to) < iso) return;
    hit = v;
  });
  return hit;
}

function spuIndSourcesFingerprint(sources) {
  return JSON.stringify((sources || []).map(function (s) {
    if (!s) return null;
    return [s.source_type || "", s.source_metric_id || "", s.source_result || ""];
  }));
}

function spuIndLaterVersionsAfter(it, fromIso) {
  const from = String(fromIso || "");
  return ((it && it.source_versions) || []).filter(function (v) {
    return v && String(v.effective_from || "") > from;
  }).sort(function (a, b) {
    return String(a.effective_from || "").localeCompare(String(b.effective_from || ""));
  });
}

function spuIndLaterVersionsWithDivergentSources(it, fromIso, draftSources) {
  const draftFp = spuIndSourcesFingerprint(draftSources);
  return spuIndLaterVersionsAfter(it, fromIso).filter(function (v) {
    return spuIndSourcesFingerprint(v.sources) !== draftFp;
  });
}

function spuVersionRecordWrite(target, body) {
  target.sources = (body.sources || []).map(function (s) { return spuNewSourceRow(s); });
  target.aggregation_type = body.aggregation_type || "sum";
  target.calc_mode = body.calc_mode || body.aggregation_type || "sum";
  target.formula_expr = body.formula_expr || [];
  target.plan_formula_expr = body.plan_formula_expr || [];
  target.plan_calc_mode = body.plan_calc_mode === "formula" ? "formula" : "manual";
}

function fingerprint(cfg) {
  return spuIndSourcesFingerprint(cfg && cfg.sources);
}

function insertSegment(it, from, mid) {
  const body = {
    sources: [spuNewSourceRow({ source_metric_id: mid, source_result: "metric_overall" })],
    aggregation_type: "sum",
    calc_mode: "sum"
  };
  const covering = (it.source_versions || []).filter(function (v) {
    return spuVersionCoversDay(v, from);
  });
  assert(covering.length === 1, "covering");
  const V = covering[0];
  const later = spuIndLaterVersionsAfter(it, from);
  assert(later.length >= 1, "need later for insert_segment");
  const next = later[0];
  const segTo = isoAddDays(String(next.effective_from), -1);
  V.effective_to = isoAddDays(from, -1);
  const rec = {
    version_id: "seg_" + from,
    effective_from: from,
    effective_to: segTo,
    sources: body.sources.slice()
  };
  it.source_versions.push(rec);
  it.source_versions.sort(function (a, b) {
    return String(a.effective_from).localeCompare(String(b.effective_from));
  });
  return { mode: "insert_segment", rec: rec, next: next };
}

function propagate(it, from, mid) {
  const draft = [spuNewSourceRow({ source_metric_id: mid, source_result: "metric_overall" })];
  spuIndLaterVersionsAfter(it, from).forEach(function (v) {
    spuVersionRecordWrite(v, {
      sources: draft,
      aggregation_type: "sum",
      calc_mode: "sum",
      formula_expr: v.formula_expr || [],
      plan_formula_expr: v.plan_formula_expr || [],
      plan_calc_mode: v.plan_calc_mode || "manual"
    });
  });
}

function savedOpenFrom(it) {
  const open = (it.source_versions || []).filter(function (v) { return v && !v.effective_to; })
    .sort(function (a, b) {
      return String(a.effective_from).localeCompare(String(b.effective_from));
    });
  return open.length ? open[open.length - 1].effective_from : "";
}

function formMidAt(it, fromThu) {
  const asOf = isoAddDays(fromThu, 6);
  const v = spuVersionAt(it, asOf);
  return v && v.sources && v.sources[0] && v.sources[0].source_metric_id;
}

function overallMode(mid) {
  if (mid === "metric_writeoff_losses") return "month_money_unsupported";
  if (mid === "store_metric_inventory_result") return "inventory_shop";
  if (mid === "store_metric_visitors") return "store";
  return "other";
}

// --- A: bug path until_next: save@12 inventory, open@19 writeoff remains ---
{
  const it = {
    indicator_id: "mind_097",
    source_versions: [
      {
        version_id: "v_cover",
        effective_from: "2026-11-05",
        effective_to: "",
        sources: [spuNewSourceRow({
          source_metric_id: "metric_writeoff_losses",
          source_result: "department_total"
        })]
      },
      {
        version_id: "v_open19",
        effective_from: "2026-11-19",
        effective_to: "",
        sources: [spuNewSourceRow({
          source_metric_id: "metric_writeoff_losses",
          source_result: "department_total"
        })]
      }
    ]
  };
  const draft = [spuNewSourceRow({
    source_metric_id: "store_metric_inventory_result",
    source_result: "metric_overall"
  })];
  const div = spuIndLaterVersionsWithDivergentSources(it, "2026-11-12", draft);
  assert(div.length === 1 && div[0].effective_from === "2026-11-19", "A: divergent later@19");
  insertSegment(it, "2026-11-12", "store_metric_inventory_result");
  assert(formMidAt(it, "2026-11-12") === "store_metric_inventory_result", "A: W1 inventory");
  assert(formMidAt(it, "2026-11-19") === "metric_writeoff_losses", "A: W2 still writeoff without propagate");
  assert(savedOpenFrom(it) === "2026-11-19", "A: reopen date = last open 19");
  assert(overallMode(formMidAt(it, "2026-11-19")) === "month_money_unsupported", "A: W2 empty fact path");
}

// --- B: propagate fixes reopen + both weeks ---
{
  const it = {
    indicator_id: "mind_097",
    source_versions: [
      {
        version_id: "v_cover",
        effective_from: "2026-11-05",
        effective_to: "",
        sources: [spuNewSourceRow({
          source_metric_id: "metric_writeoff_losses",
          source_result: "department_total"
        })]
      },
      {
        version_id: "v_open19",
        effective_from: "2026-11-19",
        effective_to: "",
        sources: [spuNewSourceRow({
          source_metric_id: "metric_writeoff_losses",
          source_result: "department_total"
        })]
      }
    ]
  };
  insertSegment(it, "2026-11-12", "store_metric_inventory_result");
  propagate(it, "2026-11-12", "store_metric_inventory_result");
  assert(formMidAt(it, "2026-11-12") === "store_metric_inventory_result", "B: W1 inventory");
  assert(formMidAt(it, "2026-11-19") === "store_metric_inventory_result", "B: W2 inventory after propagate");
  assert(overallMode(formMidAt(it, "2026-11-19")) === "inventory_shop", "B: W2 overall inventory");
  /* reopen date still last open 19, but mid matches saved inventory */
  assert(savedOpenFrom(it) === "2026-11-19", "B: open from still 19");
  assert(formMidAt(it, savedOpenFrom(it)) === "store_metric_inventory_result", "B: reopen mid inventory");
}

// --- C: visitors single open — no divergent later ---
{
  const it = {
    source_versions: [{
      version_id: "v",
      effective_from: "2026-11-12",
      effective_to: "",
      sources: [spuNewSourceRow({
        source_metric_id: "store_metric_visitors",
        source_result: "metric_overall"
      })]
    }]
  };
  const draft = [spuNewSourceRow({
    source_metric_id: "store_metric_visitors",
    source_result: "metric_overall"
  })];
  assert(spuIndLaterVersionsWithDivergentSources(it, "2026-11-12", draft).length === 0, "C: no divergent");
  assert(formMidAt(it, "2026-11-12") === "store_metric_visitors", "C: W1 visitors");
  assert(formMidAt(it, "2026-11-19") === "store_metric_visitors", "C: W2 visitors");
}

// --- D: week-aligned form cfg (asOf = Wed) ---
{
  assert(isoAddDays("2026-11-12", 6) === "2026-11-18", "D: week end 18");
  assert(isoAddDays("2026-11-19", 6) === "2026-11-25", "D: week end 25");
}

console.log("wt_spu_worktime_source_save_reopen_harness: OK");
console.log("  A until_next leaves writeoff@19 (bug path documented)");
console.log("  B propagate → inventory on W1 and W2; reopen mid matches");
console.log("  C visitors single open unchanged");
console.log("  D week-end asOf mapping");
