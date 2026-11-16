/**
 * СПУ indicators persist: SoftSync не откатывает dirty; snapshot overlay
 * сохраняет group_total/sales даже если live откатили; roundtrip open
 * читает source_versions; department_total / manual / calculated не ломаются;
 * исторические closed versions не трогаем.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m || "assert"); }
function clone(x) { return JSON.parse(JSON.stringify(x)); }

function wtSoftMergePreferServer(server, draft, dirty, path) {
  path = path || "";
  if (path && dirty[path]) {
    return draft === undefined ? clone(server) : clone(draft);
  }
  if (Array.isArray(server) || Array.isArray(draft)) {
    return clone(server);
  }
  if (server && typeof server === "object" || draft && typeof draft === "object") {
    const sObj = (server && typeof server === "object" && !Array.isArray(server)) ? server : {};
    const dObj = (draft && typeof draft === "object" && !Array.isArray(draft)) ? draft : {};
    const out = {};
    const keys = {};
    Object.keys(sObj).forEach(function (k) { keys[k] = true; });
    Object.keys(dObj).forEach(function (k) { keys[k] = true; });
    Object.keys(keys).forEach(function (k) {
      const child = path ? path + "." + k : k;
      if (Object.prototype.hasOwnProperty.call(sObj, k)) {
        out[k] = wtSoftMergePreferServer(sObj[k], dObj[k], dirty, child);
      } else if (dirty[child]) {
        out[k] = clone(dObj[k]);
      }
    });
    return out;
  }
  return clone(server);
}

function overlayLiveRows(baseList, liveList, ids) {
  const want = {};
  (ids || []).forEach(function (id) { if (id) want[String(id)] = true; });
  const liveById = {};
  (liveList || []).forEach(function (x) {
    if (x && x.indicator_id != null) liveById[String(x.indicator_id)] = x;
  });
  const seen = {};
  const out = [];
  (Array.isArray(baseList) ? baseList : []).forEach(function (it) {
    if (!it) return;
    const id = String(it.indicator_id || "");
    if (want[id]) {
      seen[id] = true;
      out.push(clone(liveById[id] || it));
      return;
    }
    out.push(clone(it));
  });
  Object.keys(want).forEach(function (id) {
    if (seen[id] || !liveById[id]) return;
    out.push(clone(liveById[id]));
  });
  return out;
}

/** Mirror wtSpuIndicatorsOverlayFrozenRows */
function overlayFrozenRows(baseList, ids, frozenRows) {
  const want = {};
  (ids || []).forEach(function (id) { if (id) want[String(id)] = true; });
  const frozenById = {};
  (Array.isArray(frozenRows) ? frozenRows : []).forEach(function (x) {
    if (!x || x.indicator_id == null) return;
    const id = String(x.indicator_id);
    if (want[id]) frozenById[id] = x;
  });
  const seen = {};
  const out = [];
  (Array.isArray(baseList) ? baseList : []).forEach(function (it) {
    if (!it) return;
    const id = String(it.indicator_id || "");
    if (want[id]) {
      seen[id] = true;
      out.push(clone(frozenById[id] || it));
      return;
    }
    out.push(clone(it));
  });
  Object.keys(want).forEach(function (id) {
    if (seen[id] || !frozenById[id]) return;
    out.push(clone(frozenById[id]));
  });
  return out;
}

function syncFromDomResult(resultRaw) {
  let sourceResult = "department_total";
  let workTab = "";
  const raw = String(resultRaw || "department_total");
  if (raw.indexOf("group:") === 0) {
    sourceResult = "group_total";
    workTab = raw.slice(6);
  } else if (raw === "fact") sourceResult = "fact";
  else if (raw === "metric_total") sourceResult = "metric_total";
  return { source_result: sourceResult, source_work_tab_id: workTab };
}

function spuActiveConfigSources(it, iso) {
  const vers = (it && Array.isArray(it.source_versions)) ? it.source_versions : [];
  let hit = null;
  const day = iso || "2026-11-16";
  vers.forEach(function (v) {
    if (!v) return;
    if (v.effective_from && String(v.effective_from) > day) return;
    if (v.effective_to && String(v.effective_to) < day) return;
    hit = v;
  });
  if (hit && hit.sources && hit.sources.length) return hit.sources;
  return (it && it.sources) || [];
}

function commitSourceVersion(it, nextSources, today) {
  today = today || "2026-11-16";
  if (!Array.isArray(it.source_versions)) it.source_versions = [];
  const payloadSources = clone(nextSources || []);
  if (!it.source_versions.length) {
    it.source_versions.push({
      version_id: "sver_1",
      effective_from: "2000-01-01",
      effective_to: "",
      sources: payloadSources
    });
    return { mode: "create_legacy" };
  }
  const open = it.source_versions.filter(function (v) { return v && !v.effective_to; });
  const cur = open.length ? open[open.length - 1] : it.source_versions[it.source_versions.length - 1];
  const fp = function (srcs) {
    return JSON.stringify((srcs || []).map(function (s) {
      return [s.source_result, s.source_work_tab_id, s.source_metric_id];
    }));
  };
  if (cur && fp(cur.sources) === fp(payloadSources)) return { mode: "noop" };
  if (cur && String(cur.effective_from || "") >= today) {
    cur.sources = payloadSources;
    return { mode: "inplace_today" };
  }
  if (cur) {
    const d = new Date(today + "T12:00:00Z");
    d.setUTCDate(d.getUTCDate() - 1);
    cur.effective_to = d.toISOString().slice(0, 10);
  }
  it.source_versions.push({
    version_id: "sver_" + (it.source_versions.length + 1),
    effective_from: today,
    effective_to: "",
    sources: payloadSources
  });
  return { mode: "split_today" };
}

const neighbor = {
  indicator_id: "mind_other",
  name: "Other",
  source_result: "department_total",
  sources: [{ source_id: "o1", source_type: "worktime_metric", source_metric_id: "m_x", source_result: "department_total", source_work_tab_id: "" }]
};

const baseServerInd = {
  indicator_id: "mind_opt",
  name: "Выручка ОПТ",
  source_type: "worktime_metric",
  source_metric_id: "m_opt",
  source_result: "department_total",
  source_work_tab_id: "",
  sources: [{
    source_id: "ssrc_1",
    source_type: "worktime_metric",
    source_metric_id: "m_opt",
    source_result: "department_total",
    source_work_tab_id: ""
  }],
  source_versions: [{
    version_id: "sver_old",
    effective_from: "2000-01-01",
    effective_to: "",
    sources: [{
      source_id: "ssrc_1",
      source_type: "worktime_metric",
      source_metric_id: "m_opt",
      source_result: "department_total",
      source_work_tab_id: ""
    }]
  }]
};

// --- 1) DOM sync group:sales ---
{
  const synced = syncFromDomResult("group:sales");
  assert(synced.source_result === "group_total", "dom → group_total");
  assert(synced.source_work_tab_id === "sales", "dom → sales");
}

// --- 2) SoftSync без dirty откатывает; с dirty — сохраняет ---
{
  const serverPayload = {
    management: { indicators: [clone(baseServerInd), clone(neighbor)] }
  };
  const draftLive = {
    management: {
      indicators: [
        Object.assign(clone(baseServerInd), {
          source_result: "group_total",
          source_work_tab_id: "sales",
          sources: [{
            source_id: "ssrc_1",
            source_type: "worktime_metric",
            source_metric_id: "m_opt",
            source_result: "group_total",
            source_work_tab_id: "sales"
          }]
        }),
        clone(neighbor)
      ]
    }
  };
  const rolled = wtSoftMergePreferServer(serverPayload, draftLive, {}, "");
  const rolledInd = rolled.management.indicators.find(function (x) { return x.indicator_id === "mind_opt"; });
  assert(rolledInd.source_result === "department_total", "no dirty → SoftSync откат");

  const kept = wtSoftMergePreferServer(serverPayload, draftLive, { "management.indicators": true }, "");
  const keptInd = kept.management.indicators.find(function (x) { return x.indicator_id === "mind_opt"; });
  assert(keptInd.source_result === "group_total", "dirty → SoftSync сохраняет group_total");
  assert(keptInd.source_work_tab_id === "sales", "dirty → SoftSync сохраняет sales");
}

// --- 3) Snapshot: live откатили после freeze — OPS всё равно шлёт group_total ---
{
  const lastLoaded = [clone(baseServerInd), clone(neighbor)];
  let live = [
    Object.assign(clone(baseServerInd), {
      source_result: "group_total",
      source_work_tab_id: "sales",
      sources: [{
        source_id: "ssrc_1",
        source_type: "worktime_metric",
        source_metric_id: "m_opt",
        source_result: "group_total",
        source_work_tab_id: "sales"
      }],
      source_versions: [{
        version_id: "sver_new",
        effective_from: "2026-11-16",
        effective_to: "",
        sources: [{
          source_id: "ssrc_1",
          source_type: "worktime_metric",
          source_metric_id: "m_opt",
          source_result: "group_total",
          source_work_tab_id: "sales"
        }]
      }]
    }),
    clone(neighbor)
  ];
  const frozen = overlayLiveRows(lastLoaded, live, ["mind_opt"]);
  // SoftSync откатил live
  live = [clone(baseServerInd), clone(neighbor)];
  const lateLiveBuild = overlayLiveRows(lastLoaded, live, ["mind_opt"]);
  const lateHit = lateLiveBuild.find(function (x) { return x.indicator_id === "mind_opt"; });
  assert(lateHit.source_result === "department_total", "late live build уже испорчен");

  const snapHit = frozen.find(function (x) { return x.indicator_id === "mind_opt"; });
  assert(snapHit.source_result === "group_total", "frozen сохраняет group_total");
  assert(snapHit.source_work_tab_id === "sales", "frozen сохраняет sales");
  assert(snapHit.sources[0].source_result === "group_total", "frozen sources");
}

// --- 4) Offline/timeout: нет ложного success, dirty остаётся ---
{
  function handleRes(res, dirty) {
    let successToast = false;
    let cleared = false;
    if (res && res.status === "saved") {
      delete dirty["management.indicators"];
      cleared = true;
      successToast = true;
      return { ok: true, successToast: successToast, cleared: cleared, dirty: dirty };
    }
    if (res && (res.status === "offline" || res.error === "network")) {
      return { ok: false, successToast: false, cleared: false, dirty: dirty };
    }
    return { ok: false, successToast: false, cleared: false, dirty: dirty };
  }
  const dirty = { "management.indicators": true };
  const fail = handleRes({ status: "offline", error: "network" }, dirty);
  assert(!fail.ok && !fail.successToast, "timeout → не success");
  assert(dirty["management.indicators"], "timeout → dirty остаётся");
  const ok = handleRes({ status: "saved" }, { "management.indicators": true });
  assert(ok.ok && ok.successToast && ok.cleared, "saved → success + clear dirty");
}

// --- 5) Conflict 409: rebuild из frozen, не из откатанного live ---
{
  const frozen = [
    Object.assign(clone(baseServerInd), {
      source_result: "group_total",
      source_work_tab_id: "sales",
      sources: [{ source_id: "ssrc_1", source_type: "worktime_metric", source_metric_id: "m_opt", source_result: "group_total", source_work_tab_id: "sales" }]
    }),
    clone(neighbor)
  ];
  const serverNewer = [
    Object.assign(clone(baseServerInd), { name: "Выручка ОПТ (server rename)" }),
    clone(neighbor),
    { indicator_id: "mind_new_server", name: "New" }
  ];
  const rebuilt = overlayFrozenRows(serverNewer, ["mind_opt"], frozen);
  const hit = rebuilt.find(function (x) { return x.indicator_id === "mind_opt"; });
  assert(hit.source_result === "group_total", "409 rebuild → group_total из frozen");
  assert(hit.source_work_tab_id === "sales", "409 rebuild → sales");
  assert(rebuilt.some(function (x) { return x.indicator_id === "mind_new_server"; }), "409 сохраняет чужие строки сервера");
  assert(rebuilt.filter(function (x) { return x.indicator_id === "mind_opt"; }).length === 1, "409 без дубля");
}

// --- 6) Roundtrip: save group_total → open через spuActiveConfig ---
{
  const it = clone(baseServerInd);
  const nextSources = [{
    source_id: "ssrc_1",
    source_type: "worktime_metric",
    source_metric_id: "m_opt",
    source_result: "group_total",
    source_work_tab_id: "sales"
  }];
  it.source_result = "group_total";
  it.source_work_tab_id = "sales";
  it.sources = clone(nextSources);
  const closedBefore = clone(it.source_versions[0]);
  const mode = commitSourceVersion(it, nextSources, "2026-11-16");
  assert(mode.mode === "split_today" || mode.mode === "inplace_today" || mode.mode === "create_legacy", "version commit ok");
  const openSrc = spuActiveConfigSources(it, "2026-11-16");
  assert(openSrc[0].source_result === "group_total", "reopen active → group_total");
  assert(openSrc[0].source_work_tab_id === "sales", "reopen active → sales");
  // Историческая версия: либо закрыта с прежним содержимым, либо inplace на той же записи
  if (mode.mode === "split_today") {
    const old = it.source_versions.find(function (v) { return v.version_id === "sver_old"; });
    assert(old && old.effective_to, "old version closed");
    assert(old.sources[0].source_result === "department_total", "old version content unchanged");
    assert(closedBefore.sources[0].source_result === "department_total", "pre-save old snapshot");
  }
}

// --- 7) department_total обычный показатель сохраняется ---
{
  const it = clone(baseServerInd);
  const src = [{
    source_id: "ssrc_1",
    source_type: "worktime_metric",
    source_metric_id: "m_opt",
    source_result: "department_total",
    source_work_tab_id: ""
  }];
  it.source_result = "department_total";
  it.source_work_tab_id = "";
  it.sources = src;
  commitSourceVersion(it, src, "2026-11-16");
  const openSrc = spuActiveConfigSources(it, "2026-11-16");
  assert(openSrc[0].source_result === "department_total", "dept_total roundtrip");
  assert(openSrc[0].source_work_tab_id === "", "dept_total tab empty");
}

// --- 8) manual / calculated: overlay snapshot не теряет kind-поля ---
{
  const manual = {
    indicator_id: "mind_man",
    name: "Ручной",
    indicator_kind: "manual",
    source_type: "manual",
    sources: []
  };
  const calc = {
    indicator_id: "mind_calc",
    name: "Расчёт",
    indicator_kind: "calculated",
    source_type: "calculated",
    aggregation_type: "formula",
    formula_expr: [{ t: "ind", id: "mind_man" }],
    sources: [{ source_id: "s1", source_type: "spu_indicator", source_indicator_id: "mind_man" }],
    source_versions: [{
      version_id: "sver_c",
      effective_from: "2000-01-01",
      effective_to: "",
      sources: [{ source_id: "s1", source_type: "spu_indicator", source_indicator_id: "mind_man" }],
      formula_expr: [{ t: "ind", id: "mind_man" }]
    }]
  };
  const frozenMan = overlayLiveRows([clone(manual), clone(neighbor)], [clone(manual), clone(neighbor)], ["mind_man"]);
  const frozenCalc = overlayLiveRows([clone(calc), clone(neighbor)], [clone(calc), clone(neighbor)], ["mind_calc"]);
  assert(frozenMan.find(function (x) { return x.indicator_id === "mind_man"; }).source_type === "manual", "manual overlay");
  assert(frozenCalc.find(function (x) { return x.indicator_id === "mind_calc"; }).formula_expr.length === 1, "calculated overlay");
  const rebuiltCalc = overlayFrozenRows(
    [Object.assign(clone(calc), { name: "server" }), clone(neighbor)],
    ["mind_calc"],
    frozenCalc
  );
  assert(rebuiltCalc.find(function (x) { return x.indicator_id === "mind_calc"; }).formula_expr[0].id === "mind_man", "calc 409 keeps formula");
}

console.log("wt_spu_indicator_softsync_persist_harness: OK");
