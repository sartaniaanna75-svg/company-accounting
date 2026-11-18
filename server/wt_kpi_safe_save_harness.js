/**
 * KPI safe save: SoftSync keyed + create markers + D18 outcome (синтетика).
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m || "assert"); }
function clone(x) { return JSON.parse(JSON.stringify(x)); }

function wtSaveOutcomeOk(ok) { return ok === true; }

function wtKpiListCreateMarkers(path, items, lastLoaded) {
  const createIds = [];
  const createKpiIds = [];
  const prev = (lastLoaded && lastLoaded[path]) || [];
  const prevIds = {};
  const prevKpiIds = {};
  prev.forEach(function (row) {
    if (!row) return;
    if (row.id != null) prevIds[String(row.id)] = true;
    if (row.kpi_id != null) prevKpiIds[String(row.kpi_id)] = true;
  });
  (items || []).forEach(function (row) {
    if (!row || row.id == null) return;
    const id = String(row.id);
    if (prevIds[id]) return;
    createIds.push(id);
    const kid = row.kpi_id != null ? String(row.kpi_id) : "";
    if (kid && !prevKpiIds[kid] && createKpiIds.indexOf(kid) === -1) createKpiIds.push(kid);
  });
  return { createIds: createIds, createKpiIds: createKpiIds };
}

// Brand-new rule → createIds + createKpiIds
{
  const last = { "shopPayroll.kpiDefs": [{ id: "kpv_001", kpi_id: "kpi_001", amount: 100 }] };
  const items = [
    { id: "kpv_001", kpi_id: "kpi_001", amount: 100 },
    { id: "kpv_002_abc", kpi_id: "kpi_002", amount: 50 }
  ];
  const m = wtKpiListCreateMarkers("shopPayroll.kpiDefs", items, last);
  assert(m.createIds.indexOf("kpv_002_abc") !== -1, "create id");
  assert(m.createKpiIds.indexOf("kpi_002") !== -1, "create kpi_id");
}

// Mutate new version: createIds yes, createKpiIds no
{
  const last = { "shopPayroll.kpiDefs": [{ id: "kpv_001", kpi_id: "kpi_001", amount: 100 }] };
  const items = [
    { id: "kpv_001", kpi_id: "kpi_001", amount: 100, validTo: "2026-10" },
    { id: "kpv_003_xyz", kpi_id: "kpi_001", amount: 120 }
  ];
  const m = wtKpiListCreateMarkers("shopPayroll.kpiDefs", items, last);
  assert(m.createIds.indexOf("kpv_003_xyz") !== -1, "version create id");
  assert(m.createKpiIds.indexOf("kpi_001") === -1, "no create_kpi_ids on mutate");
}

// D18
assert(wtSaveOutcomeOk("timeout") === false, "timeout");
assert(wtSaveOutcomeOk(true) === true, "true");

// SoftSync keyed keep sibling (simplified 3-way)
function wtKeyedListMerge3(baseList, localList, serverList, key, resolveConflicts) {
  const B = {}, L = {}, S = {};
  function idx(list, map) {
    (list || []).forEach(function (r) {
      if (r && r[key] != null) map[String(r[key])] = r;
    });
  }
  idx(baseList, B); idx(localList, L); idx(serverList, S);
  const ids = {};
  Object.keys(B).concat(Object.keys(L), Object.keys(S)).forEach(function (id) { ids[id] = true; });
  const out = [];
  Object.keys(ids).forEach(function (id) {
    const inB = Object.prototype.hasOwnProperty.call(B, id);
    const inL = Object.prototype.hasOwnProperty.call(L, id);
    const inS = Object.prototype.hasOwnProperty.call(S, id);
    if (inB && inL && inS) {
      const lSame = JSON.stringify(L[id]) === JSON.stringify(B[id]);
      const sSame = JSON.stringify(S[id]) === JSON.stringify(B[id]);
      if (lSame) out.push(clone(S[id]));
      else if (sSame) out.push(clone(L[id]));
      else if (resolveConflicts === "mine") out.push(clone(L[id]));
      else if (resolveConflicts === "server") out.push(clone(S[id]));
    } else if (!inB && inL && !inS) out.push(clone(L[id]));
    else if (!inB && !inL && inS) out.push(clone(S[id]));
    else if (inB && inL && !inS) { /* local kept if changed — skip delete vs unchanged */ if (JSON.stringify(L[id]) !== JSON.stringify(B[id]) && resolveConflicts === "mine") out.push(clone(L[id])); }
    else if (inB && !inL && inS) { if (JSON.stringify(S[id]) !== JSON.stringify(B[id]) && resolveConflicts === "mine") { /* local deleted */ } else if (JSON.stringify(S[id]) === JSON.stringify(B[id])) { /* deleted */ } else if (resolveConflicts === "mine") { /* conflict delete */ } else out.push(clone(S[id])); }
    else if (!inB && inL && inS) out.push(clone(resolveConflicts === "mine" ? L[id] : S[id]));
  });
  return out;
}

{
  const base = [{ id: "kpv_a", kpi_id: "kpi_a", amount: 10 }];
  const local = [{ id: "kpv_a", kpi_id: "kpi_a", amount: 10 }, { id: "kpv_b", kpi_id: "kpi_b", amount: 20 }];
  const server = [{ id: "kpv_a", kpi_id: "kpi_a", amount: 10 }, { id: "kpv_c", kpi_id: "kpi_c", amount: 30 }];
  const merged = wtKeyedListMerge3(base, local, server, "id", "mine");
  const ids = merged.map(function (r) { return r.id; });
  assert(ids.indexOf("kpv_b") !== -1, "keep local create");
  assert(ids.indexOf("kpv_c") !== -1, "keep server create");
}

console.log("wt_kpi_safe_save_harness: OK");
