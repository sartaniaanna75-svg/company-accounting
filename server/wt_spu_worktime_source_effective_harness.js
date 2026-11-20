/**
 * WorkTime СПУ: неделя начала действия источника через source_versions / effectiveFrom.
 * Зеркало spuCommitSourceVersion + spuVersionAt + split_tail. Без БД/DOM.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function isoAddDays(iso, n) {
  const p = String(iso).split("-").map(Number);
  const d = new Date(p[0], p[1] - 1, p[2] + Number(n || 0));
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return d.getFullYear() + "-" + mm + "-" + dd;
}

function spuVersionCoversDay(v, day) {
  if (!v) return false;
  if (v.effective_from && String(v.effective_from) > day) return false;
  if (v.effective_to && String(v.effective_to) < day) return false;
  return true;
}

function spuVersionAt(it, iso) {
  const vers = (it && Array.isArray(it.source_versions)) ? it.source_versions : [];
  if (!vers.length) return null;
  const day = iso;
  let hit = null;
  vers.forEach(function (v) {
    if (!v) return;
    if (v.effective_from && String(v.effective_from) > day) return;
    if (v.effective_to && String(v.effective_to) < day) return;
    hit = v;
  });
  return hit;
}

function fingerprint(payload) {
  return JSON.stringify({
    s: (payload.sources || []).map(function (s) {
      return [s.source_metric_id, s.source_result, s.source_work_tab_id];
    }),
    a: payload.aggregation_type
  });
}

function accountingWeekStart(iso) {
  const p = String(iso).split("-").map(Number);
  const d = new Date(p[0], p[1] - 1, p[2]);
  const offset = (d.getDay() - 4 + 7) % 7; // Thursday=4
  d.setDate(d.getDate() - offset);
  return d.getFullYear() + "-"
    + String(d.getMonth() + 1).padStart(2, "0") + "-"
    + String(d.getDate()).padStart(2, "0");
}

const LEGACY_FROM = "2026-11-05";

/** Зеркало spuIndSavedEffectiveFromIso для WorkTime. */
function savedEffectiveFromIso(it) {
  const vers = (it && Array.isArray(it.source_versions)) ? it.source_versions.filter(Boolean) : [];
  if (vers.length) {
    const open = vers.filter(function (v) { return !v.effective_to; }).slice().sort(function (a, b) {
      return String(a.effective_from || "").localeCompare(String(b.effective_from || ""));
    });
    const pick = open.length ? open[open.length - 1] : vers[0];
    let iso = pick && pick.effective_from ? String(pick.effective_from) : "";
    if (iso && iso < LEGACY_FROM) iso = LEGACY_FROM;
    return iso || LEGACY_FROM;
  }
  return LEGACY_FROM;
}

/** Упрощённый commit с effectiveFrom (create / split_tail / inplace / noop / extend_back). */
function commitWithEffectiveFrom(it, sources, from, confirm) {
  const payload = {
    sources: sources,
    aggregation_type: "sum",
    formula_expr: [],
    calc_mode: "sum",
    plan_formula_expr: [],
    plan_calc_mode: "manual"
  };
  if (!Array.isArray(it.source_versions)) it.source_versions = [];
  const bodyFp = fingerprint(payload);

  if (!it.source_versions.length) {
    it.source_versions.push({
      version_id: "sver_1",
      effective_from: from,
      effective_to: "",
      sources: JSON.parse(JSON.stringify(sources)),
      aggregation_type: "sum"
    });
    return { ok: true, mode: "create" };
  }

  const sameFrom = it.source_versions.filter(function (v) {
    return v && String(v.effective_from || "") === from;
  });
  if (sameFrom.length === 1) {
    const cur = sameFrom[0];
    if (fingerprint(cur) === bodyFp) return { ok: true, mode: "noop" };
    if (!confirm) return { ok: false, needsConfirm: true, mode: "inplace" };
    cur.sources = JSON.parse(JSON.stringify(sources));
    cur.aggregation_type = "sum";
    return { ok: true, mode: "inplace" };
  }

  const covering = it.source_versions.filter(function (v) { return spuVersionCoversDay(v, from); });
  if (!covering.length) {
    const sorted = it.source_versions.slice().sort(function (a, b) {
      return String((a && a.effective_from) || "").localeCompare(String((b && b.effective_from) || ""));
    });
    const earliest = sorted[0];
    if (earliest && String(earliest.effective_from || "") > from) {
      if (!confirm) return { ok: false, needsConfirm: true, mode: "extend_back" };
      earliest.effective_from = from;
      earliest.sources = JSON.parse(JSON.stringify(sources));
      earliest.aggregation_type = "sum";
      return { ok: true, mode: "extend_back" };
    }
    throw new Error("gap not extendable at " + from);
  }
  assert(covering.length === 1, "one covering at " + from);
  const V = covering[0];
  if (fingerprint(V) === bodyFp) return { ok: true, mode: "noop" };
  const later = it.source_versions.filter(function (v) {
    return v && String(v.effective_from || "") > from;
  });
  if (!later.length) {
    if (!confirm) return { ok: false, needsConfirm: true, mode: "split_tail" };
    V.effective_to = isoAddDays(from, -1);
    it.source_versions.push({
      version_id: "sver_" + (it.source_versions.length + 1),
      effective_from: from,
      effective_to: "",
      sources: JSON.parse(JSON.stringify(sources)),
      aggregation_type: "sum"
    });
    return { ok: true, mode: "split_tail" };
  }
  throw new Error("insert_segment not covered in this harness");
}

const srcOverall = [{
  source_id: "ssrc_1",
  source_type: "worktime_metric",
  source_metric_id: "metric_display_positions",
  source_result: "metric_overall",
  source_work_tab_id: ""
}];
const srcGroup = [{
  source_id: "ssrc_1",
  source_type: "worktime_metric",
  source_metric_id: "metric_display_positions",
  source_result: "group_total",
  source_work_tab_id: "pickers"
}];

// 1) Новая настройка: create с недели 19.11 → действует 19–25 и далее
{
  const it = { indicator_id: "ind_wt", source_versions: [] };
  const from = accountingWeekStart("2026-11-20");
  assert(from === "2026-11-19", "1: default week Thu " + from);
  const r = commitWithEffectiveFrom(it, srcOverall, from, true);
  assert(r.mode === "create", "1: create");
  assert(it.source_versions.length === 1, "1: one version");
  assert(it.source_versions[0].effective_from === "2026-11-19", "1: from 19");
  assert(it.source_versions[0].sources[0].source_result === "metric_overall", "1: overall stored");
  assert(spuVersionAt(it, "2026-11-25").sources[0].source_result === "metric_overall", "1: week end 25");
  assert(spuVersionAt(it, "2026-12-02").sources[0].source_result === "metric_overall", "1: next week");
}

// 2) Следующая неделя без нового save — та же версия
{
  const it = {
    source_versions: [{
      version_id: "sver_1",
      effective_from: "2026-11-19",
      effective_to: "",
      sources: JSON.parse(JSON.stringify(srcOverall)),
      aggregation_type: "sum"
    }]
  };
  const v1 = spuVersionAt(it, "2026-11-25");
  const v2 = spuVersionAt(it, "2026-12-02");
  assert(v1.version_id === v2.version_id, "2: same version next week");
  assert(v1.sources[0].source_result === "metric_overall", "2: overall persists");
}

// 3) Изменение с 26.11 → split; 19–25 остаётся старой
{
  const it = {
    source_versions: [{
      version_id: "sver_1",
      effective_from: "2026-11-12",
      effective_to: "",
      sources: JSON.parse(JSON.stringify(srcGroup)),
      aggregation_type: "sum"
    }]
  };
  const r = commitWithEffectiveFrom(it, srcOverall, "2026-11-19", true);
  assert(r.mode === "split_tail", "3: split");
  assert(it.source_versions.length === 2, "3: two versions");
  assert(it.source_versions[0].effective_to === "2026-11-18", "3: old ends 18");
  assert(spuVersionAt(it, "2026-11-18").sources[0].source_result === "group_total", "3: week 12–18 old");
  assert(spuVersionAt(it, "2026-11-12").sources[0].source_result === "group_total", "3: first day of old");
  assert(spuVersionAt(it, "2026-11-11") === null, "3: before old from → no invent");
  assert(spuVersionAt(it, "2026-11-25").sources[0].source_result === "metric_overall", "3: week 19–25 new");
}

// 4) Open без save не мутирует versions
{
  const stored = {
    source_versions: [{
      version_id: "sver_1",
      effective_from: "2026-11-19",
      effective_to: "",
      sources: JSON.parse(JSON.stringify(srcOverall))
    }]
  };
  const draft = JSON.parse(JSON.stringify(stored));
  draft.source_versions[0].sources[0].source_result = "department_total";
  assert(stored.source_versions[0].sources[0].source_result === "metric_overall", "4: stored intact");
}

// 5) Не назначать всем старым одну дату без save
{
  const legacy = {
    source_versions: [{
      version_id: "sver_legacy",
      effective_from: "2000-01-01",
      effective_to: "",
      sources: JSON.parse(JSON.stringify(srcGroup))
    }]
  };
  assert(legacy.source_versions[0].effective_from === "2000-01-01", "5: legacy from untouched");
  assert(spuVersionAt(legacy, "2026-11-11").sources[0].source_result === "group_total", "5: still group");
}

// 6) Normalize к четвергу
{
  assert(accountingWeekStart("2026-11-20") === "2026-11-19", "6: Fri→Thu");
  assert(accountingWeekStart("2026-11-19") === "2026-11-19", "6: Thu stays");
}

// 7) Накопление: версия покрывает незавершённую неделю (to=25 при today=20)
{
  const it = {
    source_versions: [{
      version_id: "sver_1",
      effective_from: "2026-11-19",
      effective_to: "",
      sources: JSON.parse(JSON.stringify(srcOverall))
    }]
  };
  assert(spuVersionAt(it, "2026-11-20") !== null, "7: mid-week covered");
  assert(spuVersionAt(it, "2026-11-20").sources[0].source_result === "metric_overall", "7: overall mid-week");
}

// 8) Save предыдущей недели → reopen показывает сохранённую, не «текущую»
{
  const it = { indicator_id: "ind_reopen", source_versions: [] };
  const chosen = "2026-11-12";
  const currentUi = "2026-11-19";
  const r = commitWithEffectiveFrom(it, srcOverall, chosen, true);
  assert(r.mode === "create", "8: create");
  const shown = savedEffectiveFromIso(it);
  assert(shown === chosen, "8: reopen shows saved " + shown);
  assert(shown !== currentUi, "8: not replaced by current week");
}

// 9) Существующие без versions → baseline 05.11; недели 05/12/19 покрыты fallback-логикой чтения
{
  const it = { sources: JSON.parse(JSON.stringify(srcOverall)), source_versions: [] };
  assert(savedEffectiveFromIso(it) === LEGACY_FROM, "9: baseline 05.11");
  assert(spuVersionAt(it, "2026-11-05") === null, "9: no version yet");
  // После первого save с baseline — покрытие 05–11, 12–18, 19–25
  const r = commitWithEffectiveFrom(it, srcOverall, LEGACY_FROM, true);
  assert(r.mode === "create", "9: create baseline");
  ["2026-11-05", "2026-11-11", "2026-11-12", "2026-11-18", "2026-11-19", "2026-11-25", "2026-12-02"].forEach(function (d) {
    assert(spuVersionAt(it, d) && spuVersionAt(it, d).sources[0].source_result === "metric_overall", "9: cover " + d);
  });
}

// 10) Sentinel 2000-01-01 → UI baseline 05.11; данные не трогаем без save
{
  const it = {
    source_versions: [{
      version_id: "sver_legacy",
      effective_from: "2000-01-01",
      effective_to: "",
      sources: JSON.parse(JSON.stringify(srcGroup))
    }]
  };
  assert(savedEffectiveFromIso(it) === LEGACY_FROM, "10: display baseline");
  assert(it.source_versions[0].effective_from === "2000-01-01", "10: stored untouched");
}

// 11) Повторный save с той же неделей/источниками → noop, без дубля
{
  const it = {
    source_versions: [{
      version_id: "sver_1",
      effective_from: "2026-11-12",
      effective_to: "",
      sources: JSON.parse(JSON.stringify(srcOverall)),
      aggregation_type: "sum"
    }]
  };
  const r = commitWithEffectiveFrom(it, srcOverall, "2026-11-19", true);
  assert(r.mode === "noop", "11: same content covering → noop");
  assert(it.source_versions.length === 1, "11: no duplicate version");
}

// 12) Выбор более ранней недели при разрыве слева → extend_back
{
  const it = {
    source_versions: [{
      version_id: "sver_1",
      effective_from: "2026-11-19",
      effective_to: "",
      sources: JSON.parse(JSON.stringify(srcGroup)),
      aggregation_type: "sum"
    }]
  };
  const r = commitWithEffectiveFrom(it, srcOverall, LEGACY_FROM, true);
  assert(r.mode === "extend_back", "12: extend_back");
  assert(it.source_versions.length === 1, "12: no duplicate");
  assert(it.source_versions[0].effective_from === LEGACY_FROM, "12: from 05.11");
  assert(it.source_versions[0].sources[0].source_result === "metric_overall", "12: body updated");
  assert(spuVersionAt(it, "2026-11-05").sources[0].source_result === "metric_overall", "12: week 05–11");
}

console.log("wt_spu_worktime_source_effective_harness: OK");
