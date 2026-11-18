/**
 * Пакет А: SoftSync keyed merge для shopMetricCatalog.items + D18 outcome.
 * Синтетика, без сети/БД.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m || "assert"); }
function clone(x) { return JSON.parse(JSON.stringify(x)); }

function wtRecordSemEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function wtKeyedListMerge3(baseList, localList, serverList, key, resolveConflicts) {
  const base = Array.isArray(baseList) ? baseList : [];
  const local = Array.isArray(localList) ? localList : [];
  const server = Array.isArray(serverList) ? serverList : [];
  function index(list) {
    const map = {};
    for (let i = 0; i < list.length; i++) {
      const row = list[i];
      if (!row || typeof row !== "object" || row[key] == null || row[key] === "") return null;
      const id = String(row[key]);
      if (Object.prototype.hasOwnProperty.call(map, id)) return null;
      map[id] = row;
    }
    return map;
  }
  const B = index(base);
  const Lm = index(local);
  const S = index(server);
  if (!B || !Lm || !S) return { ok: false, unkeyed: true, merged: null, conflicts: [] };
  const has = function (m, id) { return Object.prototype.hasOwnProperty.call(m, id); };
  const conflicts = [];
  const pick = {};
  const ids = [];
  const seen = {};
  server.concat(local).forEach(function (row) {
    const id = String(row[key]);
    if (!seen[id]) { seen[id] = true; ids.push(id); }
  });
  Object.keys(B).forEach(function (id) {
    if (!seen[id]) { seen[id] = true; ids.push(id); }
  });
  ids.forEach(function (id) {
    const inB = has(B, id);
    const inL = has(Lm, id);
    const inS = has(S, id);
    let kind = "";
    let take;
    if (inB) {
      const lSame = inL && wtRecordSemEqual(Lm[id], B[id]);
      const sSame = inS && wtRecordSemEqual(S[id], B[id]);
      if (inL && inS) {
        if (lSame) take = S[id];
        else if (sSame) take = Lm[id];
        else if (wtRecordSemEqual(Lm[id], S[id])) take = S[id];
        else kind = "both_changed";
      } else if (!inL && inS) {
        if (sSame) take = null;
        else kind = "local_deleted_server_changed";
      } else if (inL && !inS) {
        if (lSame) take = null;
        else kind = "server_deleted_local_changed";
      } else {
        take = null;
      }
    } else if (inL && inS) {
      if (wtRecordSemEqual(Lm[id], S[id])) take = S[id];
      else kind = "both_added_different";
    } else if (inL) {
      take = Lm[id];
    } else if (inS) {
      take = S[id];
    }
    if (kind) {
      conflicts.push({ id: id, kind: kind });
      if (resolveConflicts === "mine") take = inL ? Lm[id] : null;
      else if (resolveConflicts === "server") take = inS ? S[id] : null;
      else take = undefined;
    }
    pick[id] = take;
  });
  if (conflicts.length && !resolveConflicts) {
    return { ok: false, unkeyed: false, merged: null, conflicts: conflicts };
  }
  const out = [];
  const used = {};
  server.forEach(function (row) {
    const id = String(row[key]);
    if (used[id]) return;
    used[id] = true;
    if (pick[id]) out.push(clone(pick[id]));
  });
  local.forEach(function (row) {
    const id = String(row[key]);
    if (used[id]) return;
    used[id] = true;
    if (pick[id]) out.push(clone(pick[id]));
  });
  return { ok: true, unkeyed: false, merged: out, conflicts: conflicts };
}

function wtSaveOutcomeOk(ok) { return ok === true; }

function wtMetricCatalogListSaveFeedback(ok, failMsg, successMsg, sink) {
  sink = sink || [];
  if (wtSaveOutcomeOk(ok)) {
    if (successMsg) sink.push({ type: "ok", msg: successMsg });
    return true;
  }
  if (ok === "timeout") {
    sink.push({ type: "error", msg: "timeout" });
    return false;
  }
  if (ok === "conflict_review") {
    sink.push({ type: "error", msg: "conflict" });
    return false;
  }
  sink.push({ type: "error", msg: failMsg || "fail" });
  return false;
}

// SoftSync: dirty stale local missing m_a, server has m_a (чужой create) + local m_b edit
{
  const base = [
    { id: "m_b", name: "B" }
  ];
  const local = [
    { id: "m_b", name: "B-local" }
  ];
  const server = [
    { id: "m_a", name: "A-from-other" },
    { id: "m_b", name: "B" }
  ];
  const m = wtKeyedListMerge3(base, local, server, "id", "mine");
  assert(m.ok, "merge ok");
  const ids = m.merged.map(function (r) { return r.id; });
  assert(ids.indexOf("m_a") !== -1, "keep server-only m_a");
  assert(m.merged.filter(function (r) { return r.id === "m_b"; })[0].name === "B-local", "keep local edit");
}

// Intentional local delete of m_a while server still has it
{
  const base = [
    { id: "m_a", name: "A" },
    { id: "m_b", name: "B" }
  ];
  const local = [
    { id: "m_b", name: "B" }
  ];
  const server = [
    { id: "m_a", name: "A" },
    { id: "m_b", name: "B" }
  ];
  const m = wtKeyedListMerge3(base, local, server, "id", "mine");
  assert(m.ok, "delete merge ok");
  const ids = m.merged.map(function (r) { return r.id; });
  assert(ids.indexOf("m_a") === -1, "local delete wins");
  assert(ids.indexOf("m_b") !== -1, "keep b");
}

// D18: timeout is not success
{
  const sink = [];
  assert(wtMetricCatalogListSaveFeedback("timeout", "fail", "Сохранено", sink) === false, "timeout not ok");
  assert(sink[0].type === "error" && sink[0].msg === "timeout", "timeout toast");
  const sink2 = [];
  assert(wtMetricCatalogListSaveFeedback(true, "fail", "Сохранено", sink2) === true, "true ok");
  assert(sink2[0].msg === "Сохранено", "success toast");
  assert(wtSaveOutcomeOk("timeout") === false, "outcome");
  assert(wtSaveOutcomeOk(false) === false, "false");
  assert(wtSaveOutcomeOk(true) === true, "true");
}

console.log("wt_metric_catalog_items_ops_harness: OK");
