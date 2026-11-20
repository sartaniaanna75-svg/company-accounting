/**
 * СПУ: глобальный старт отчётности 05.11.2026 — read-path gate + legacy gap-fill.
 * Без БД/DOM. Зеркало spuActiveConfig / reporting_from / классификатора.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

const SPU_REPORTING_START_ISO = "2026-11-05";

function spuReportingStartIso() {
  return SPU_REPORTING_START_ISO;
}

function spuIndicatorHasExplicitReportingFrom(it) {
  return !!(it && /^\d{4}-\d{2}-\d{2}$/.test(String(it.reporting_from || "").trim()));
}

function spuIndicatorReportingFrom(it) {
  const raw = it && String(it.reporting_from || "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  return spuReportingStartIso();
}

function spuIndicatorIsLegacyReporting(it) {
  return !spuIndicatorHasExplicitReportingFrom(it);
}

function spuIndicatorIsReportingActiveOnDay(it, day) {
  const d = String(day || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return true;
  if (d < spuReportingStartIso()) return false;
  return d >= String(spuIndicatorReportingFrom(it));
}

function spuVersionAt(it, iso) {
  const vers = (it && Array.isArray(it.source_versions)) ? it.source_versions : [];
  if (!vers.length) return null;
  let hit = null;
  vers.forEach(function (v) {
    if (!v) return;
    if (v.effective_from && String(v.effective_from) > iso) return;
    if (v.effective_to && String(v.effective_to) < iso) return;
    hit = v;
  });
  return hit;
}

function spuVersionNextAtOrAfter(it, day) {
  const vers = (it && Array.isArray(it.source_versions)) ? it.source_versions.filter(Boolean) : [];
  const d = String(day || "");
  let best = null;
  vers.forEach(function (v) {
    const f = String(v.effective_from || "");
    if (!f || f <= d) return;
    if (!best || f < String(best.effective_from || "")) best = v;
  });
  return best;
}

function fingerprint(cfg) {
  return JSON.stringify({
    s: ((cfg && cfg.sources) || []).map(function (s) {
      return [s.source_metric_id, s.source_result];
    }),
    a: cfg && cfg.aggregation_type
  });
}

function spuActiveConfigAt(it, iso) {
  if (!spuIndicatorIsReportingActiveOnDay(it, iso)) {
    return { sources: [], inactive: true, inactive_reason: "before_reporting_from" };
  }
  const v = spuVersionAt(it, iso);
  if (v) {
    return { sources: v.sources || [], inactive: false, via: "covering" };
  }
  if (spuIndicatorIsLegacyReporting(it)) {
    const next = spuVersionNextAtOrAfter(it, iso);
    if (next) return { sources: next.sources || [], inactive: false, via: "gap_next" };
  }
  return {
    sources: it.sources || [],
    inactive: false,
    via: "top_level"
  };
}

function classify(indicators) {
  const start = spuReportingStartIso();
  const out = [];
  (indicators || []).forEach(function (it) {
    if (!it || it.kind !== "worktime") return;
    const row = { id: it.indicator_id, category: "ok_from_0511" };
    if (spuIndicatorHasExplicitReportingFrom(it)) {
      out.push(row);
      return;
    }
    const vers = it.source_versions || [];
    if (!vers.length) {
      out.push(row);
      return;
    }
    const open = vers.filter(function (v) { return !v.effective_to; }).pop();
    const openFrom = open ? String(open.effective_from || "") : "";
    if (openFrom && openFrom <= start) {
      out.push(row);
      return;
    }
    const openFp = fingerprint(open || { sources: it.sources });
    const mid = vers.filter(function (v) {
      const f = String(v.effective_from || "");
      const t = v.effective_to ? String(v.effective_to) : "";
      if (!f || f >= openFrom) return false;
      if (t && t < start) return false;
      return f < openFrom && (!t || t >= start);
    });
    if (!mid.length) {
      row.category = "ok_from_0511";
      out.push(row);
      return;
    }
    const differ = mid.some(function (v) { return fingerprint(v) !== openFp; });
    row.category = differ ? "needs_confirm" : "merge_safe";
    out.push(row);
  });
  return out;
}

const srcOpen = [{ source_metric_id: "m1", source_result: "metric_overall" }];
const srcOld = [{ source_metric_id: "m1", source_result: "group_total" }];

// 1) Legacy gap: open с 19.11 → на 05 и 12 читается open (gap_next)
{
  const it = {
    indicator_id: "leg1",
    sources: srcOpen,
    source_versions: [{
      version_id: "v2",
      effective_from: "2026-11-19",
      effective_to: "",
      sources: srcOpen,
      aggregation_type: "sum"
    }]
  };
  assert(spuIndicatorReportingFrom(it) === "2026-11-05", "1: legacy from 05.11");
  const c05 = spuActiveConfigAt(it, "2026-11-05");
  const c12 = spuActiveConfigAt(it, "2026-11-12");
  const c19 = spuActiveConfigAt(it, "2026-11-19");
  assert(!c05.inactive && c05.via === "gap_next", "1: 05 gap_next");
  assert(c05.sources[0].source_result === "metric_overall", "1: 05 overall");
  assert(!c12.inactive && c12.via === "gap_next", "1: 12 gap_next");
  assert(c19.via === "covering", "1: 19 covering");
  assert(spuActiveConfigAt(it, "2026-10-29").inactive === true, "1: before global start inactive");
}

// 2) Legacy с covering A≠open на 12.11 — история сохраняется
{
  const it = {
    indicator_id: "leg2",
    sources: srcOpen,
    source_versions: [
      {
        version_id: "v1",
        effective_from: "2026-11-05",
        effective_to: "2026-11-18",
        sources: srcOld,
        aggregation_type: "sum"
      },
      {
        version_id: "v2",
        effective_from: "2026-11-19",
        effective_to: "",
        sources: srcOpen,
        aggregation_type: "sum"
      }
    ]
  };
  const c12 = spuActiveConfigAt(it, "2026-11-12");
  const c19 = spuActiveConfigAt(it, "2026-11-19");
  assert(c12.via === "covering" && c12.sources[0].source_result === "group_total", "2: 12 keeps A");
  assert(c19.sources[0].source_result === "metric_overall", "2: 19 open");
  const cls = classify([{ kind: "worktime", indicator_id: "leg2", sources: srcOpen, source_versions: it.source_versions }]);
  assert(cls[0].category === "needs_confirm", "2: classifier needs_confirm");
}

// 3) Новый с reporting_from=19.11 → 05/12 missing; 19+ считает
{
  const it = {
    indicator_id: "new1",
    reporting_from: "2026-11-19",
    sources: srcOpen,
    source_versions: [{
      version_id: "v1",
      effective_from: "2026-11-19",
      effective_to: "",
      sources: srcOpen
    }]
  };
  assert(spuActiveConfigAt(it, "2026-11-05").inactive === true, "3: 05 inactive");
  assert(spuActiveConfigAt(it, "2026-11-12").inactive === true, "3: 12 inactive");
  const c19 = spuActiveConfigAt(it, "2026-11-19");
  assert(!c19.inactive && c19.sources[0].source_result === "metric_overall", "3: 19 active");
}

// 4) Legacy без versions → top-level на 05+
{
  const it = { indicator_id: "leg0", sources: srcOpen, source_versions: [] };
  const c = spuActiveConfigAt(it, "2026-11-05");
  assert(!c.inactive && c.via === "top_level", "4: top_level");
  assert(c.sources[0].source_result === "metric_overall", "4: overall");
}

// 5) merge_safe classifier: identical mid segments
{
  const it = {
    kind: "worktime",
    indicator_id: "ms1",
    sources: srcOpen,
    source_versions: [
      {
        version_id: "v1",
        effective_from: "2026-11-05",
        effective_to: "2026-11-18",
        sources: srcOpen
      },
      {
        version_id: "v2",
        effective_from: "2026-11-19",
        effective_to: "",
        sources: srcOpen
      }
    ]
  };
  const cls = classify([it]);
  assert(cls[0].category === "merge_safe", "5: merge_safe");
}

console.log("wt_spu_reporting_start_harness: OK");
