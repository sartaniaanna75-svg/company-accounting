/**
 * saveSpuOrgIndicator persist path: narrow management OPS, no saveApp colon leaves.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

/** Mirror of server WT_PATH_RE (must stay in sync with wt_api.WT_PATH_RE). */
const WT_PATH_RE = /^[A-Za-z0-9_-]+(?:\.(?:\$[A-Za-z0-9_-]+|[A-Za-z0-9_-]+)){0,15}$/;

function pathAllowed(path) {
  return WT_PATH_RE.test(String(path || ""));
}

/** Correct SPU plan write: whole branch path; week: key lives inside value. */
function buildPlansBranchOverlay(base, periodKey, indicatorId, cellRec) {
  const out = JSON.parse(JSON.stringify(base && typeof base === "object" ? base : {}));
  const pk = String(periodKey || "");
  const iid = String(indicatorId || "");
  if (!pk || !iid) return out;
  const bucket = (out[pk] && typeof out[pk] === "object") ? Object.assign({}, out[pk]) : {};
  if (cellRec == null) delete bucket[iid];
  else bucket[iid] = cellRec;
  if (Object.keys(bucket).length) out[pk] = bucket;
  else delete out[pk];
  return out;
}

function simulateCardPersistSequence(opts) {
  const calls = [];
  const planVal = opts.planVal;
  const keepId = opts.keepId || "mind_034";
  let indOk = opts.indicatorsOk !== false;
  let planOk = opts.plansOk !== false;
  let successToast = false;
  let partialPlanToast = false;
  let modalClosed = false;

  function persistIndicators() {
    calls.push({ path: "management.indicators", ids: [keepId] });
    return indOk;
  }
  function persistPlans() {
    calls.push({ path: "management.plans", ids: [keepId] });
    return planOk;
  }

  const okInd = persistIndicators();
  if (!okInd) {
    return { calls: calls, successToast: successToast, partialPlanToast: partialPlanToast, modalClosed: modalClosed };
  }
  if (!planVal) {
    successToast = true;
    modalClosed = true;
    return { calls: calls, successToast: successToast, partialPlanToast: partialPlanToast, modalClosed: modalClosed };
  }
  const okPlan = persistPlans();
  if (!okPlan) {
    partialPlanToast = true;
    return { calls: calls, successToast: successToast, partialPlanToast: partialPlanToast, modalClosed: modalClosed };
  }
  successToast = true;
  modalClosed = true;
  return { calls: calls, successToast: successToast, partialPlanToast: partialPlanToast, modalClosed: modalClosed };
}

// Path rules
assert(pathAllowed("management.indicators"), "indicators path ok");
assert(pathAllowed("management.plans"), "plans branch path ok");
assert(!pathAllowed("management.plans.week:2026-11-06"), "week: segment rejected");
assert(!pathAllowed("management.plans.week:2026-11-06.mind_034"), "leaf with week: rejected");
assert(!pathAllowed("management.plans.month:2026-11.mind_034"), "month: rejected");
assert(!pathAllowed("management.plans.day:2026-11-06.mind_034"), "day: rejected");

// mind_034 week plan 45 — value nests under week: key, path stays management.plans
const cell = {
  indicator_id: "mind_034",
  period: "week:2026-11-06",
  period_key: "week:2026-11-06",
  plan_value: 45,
  value: "45"
};
const branch = buildPlansBranchOverlay({}, "week:2026-11-06", "mind_034", cell);
assert(branch["week:2026-11-06"].mind_034.plan_value === 45, "plan 45 nested in value");
assert(pathAllowed("management.plans"), "op path for plan write");
assert(!("week:2026-11-06" in { "management.plans": 1 }), "colon not in path string");

// Neighbor indicator untouched in overlay
const withNeighbor = {
  "week:2026-11-06": {
    mind_other: { indicator_id: "mind_other", plan_value: 10 }
  }
};
const merged = buildPlansBranchOverlay(withNeighbor, "week:2026-11-06", "mind_034", cell);
assert(merged["week:2026-11-06"].mind_other.plan_value === 10, "neighbor plan kept");
assert(merged["week:2026-11-06"].mind_034.plan_value === 45, "target plan set");

// Sequence: no plan
let r = simulateCardPersistSequence({ keepId: "mind_034", planVal: "" });
assert(r.calls.length === 1 && r.calls[0].path === "management.indicators", "no-plan: indicators only");
assert(r.successToast && r.modalClosed && !r.partialPlanToast, "no-plan success");

// Sequence: plan ok
r = simulateCardPersistSequence({ keepId: "mind_034", planVal: "45", indicatorsOk: true, plansOk: true });
assert(r.calls.length === 2, "with plan: two ops");
assert(r.calls[0].path === "management.indicators", "indicators first");
assert(r.calls[1].path === "management.plans", "plans second");
assert(r.successToast && r.modalClosed, "full success toast+close");

// Sequence: indicators fail — no success, no plan op
r = simulateCardPersistSequence({ keepId: "mind_034", planVal: "45", indicatorsOk: false });
assert(r.calls.length === 1, "ind fail stops before plans");
assert(!r.successToast && !r.modalClosed, "no false success on ind fail");

// Sequence: plans fail after indicators — no overall success, modal stays open
r = simulateCardPersistSequence({ keepId: "mind_034", planVal: "45", indicatorsOk: true, plansOk: false });
assert(r.calls.length === 2, "both attempted");
assert(!r.successToast && !r.modalClosed && r.partialPlanToast, "partial plan: no success toast / form open");

/** Mirror wtSpuIndicatorsOverlayLiveRows: upsert by indicator_id, no duplicate rows. */
function overlayIndicatorsByIds(baseList, liveById, ids) {
  const want = {};
  (ids || []).forEach(function (id) { if (id) want[String(id)] = true; });
  const seen = {};
  const out = [];
  (Array.isArray(baseList) ? baseList : []).forEach(function (it) {
    if (!it) return;
    const id = String(it.indicator_id || "");
    if (want[id]) {
      seen[id] = true;
      out.push(JSON.parse(JSON.stringify(liveById[id] || it)));
      return;
    }
    out.push(JSON.parse(JSON.stringify(it)));
  });
  Object.keys(want).forEach(function (id) {
    if (seen[id] || !liveById[id]) return;
    out.push(JSON.parse(JSON.stringify(liveById[id])));
  });
  return out;
}

// After card saved + plan failed: retry must upsert same mind_034, not duplicate
const serverAfterPartial = [
  { indicator_id: "mind_other", name: "Other", spu_layer: "key" },
  { indicator_id: "mind_034", name: "Количество полностью введённых должностей", spu_layer: "totals" }
];
const liveRetry = {
  mind_034: {
    indicator_id: "mind_034",
    name: "Количество полностью введённых должностей",
    spu_layer: "totals",
    source_type: "timesheet",
    source_timesheet_agg: "headcount"
  },
  mind_other: serverAfterPartial[0]
};
const retry1 = overlayIndicatorsByIds(serverAfterPartial, liveRetry, ["mind_034"]);
const retry2 = overlayIndicatorsByIds(retry1, liveRetry, ["mind_034"]);
assert(retry1.filter(function (x) { return x.indicator_id === "mind_034"; }).length === 1, "retry no duplicate card");
assert(retry2.filter(function (x) { return x.indicator_id === "mind_034"; }).length === 1, "second retry still one card");
assert(retry1.some(function (x) { return x.indicator_id === "mind_other"; }), "neighbor card intact");
assert(retry1.find(function (x) { return x.indicator_id === "mind_034"; }).spu_layer === "totals", "card fields kept");

// Plan retry after partial: overwrite same cell, neighbors intact
const planRetry = buildPlansBranchOverlay(merged, "week:2026-11-06", "mind_034", Object.assign({}, cell, { plan_value: 45 }));
assert(planRetry["week:2026-11-06"].mind_034.plan_value === 45, "plan retry same cell");
assert(planRetry["week:2026-11-06"].mind_other.plan_value === 10, "neighbor plan intact after retry");

r = simulateCardPersistSequence({ keepId: "mind_034", planVal: "45", indicatorsOk: true, plansOk: true });
assert(r.successToast && r.modalClosed, "retry after partial can fully succeed");

console.log("OK wt_spu_org_indicator_persist_harness");
