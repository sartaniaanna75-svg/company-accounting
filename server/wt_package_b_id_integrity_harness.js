/**
 * Пакет Б: nextPrefixedUniqueId + orphan source_metric option (синтетика).
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m || "assert"); }

function nextPrefixedId(prefix, ids) {
  let max = 0;
  const re = new RegExp("^" + prefix + "_(\\d+)$");
  (ids || []).forEach(function (id) {
    const m = String(id || "").match(re);
    if (m) max = Math.max(max, Number(m[1]));
  });
  return prefix + "_" + String(max + 1).padStart(3, "0");
}

function nextPrefixedUniqueId(prefix, ids) {
  let max = 0;
  const used = {};
  const re = new RegExp("^" + prefix + "_(\\d+)(?:_[a-z0-9]+)?$");
  (ids || []).forEach(function (id) {
    const s = String(id || "");
    used[s] = true;
    const m = s.match(re);
    if (m) max = Math.max(max, Number(m[1]));
  });
  for (let attempt = 0; attempt < 20; attempt++) {
    const tail = Math.random().toString(36).slice(2, 7).replace(/[^a-z0-9]/g, "") || String(Date.now() % 100000);
    const id = prefix + "_" + String(max + 1).padStart(3, "0") + "_" + tail;
    if (!used[id]) return id;
  }
  return prefix + "_" + String(max + 1).padStart(3, "0") + "_" + Date.now().toString(36);
}

// После hard-delete max-номера sequential reuse; unique — нет
{
  const alive = ["metric_custom_001", "metric_custom_002"];
  const afterDelete = ["metric_custom_001"]; // 002 удалён
  const seq = nextPrefixedId("metric_custom", afterDelete);
  assert(seq === "metric_custom_002", "sequential reuses deleted max");
  const u1 = nextPrefixedUniqueId("metric_custom", afterDelete);
  const u2 = nextPrefixedUniqueId("metric_custom", afterDelete.concat([u1]));
  assert(u1 !== "metric_custom_002", "unique not plain sequential");
  assert(u1 !== u2, "two creates differ");
  assert(/^metric_custom_002_[a-z0-9]+$/.test(u1), "unique shape " + u1);
}

// Два одновременных create от одного max — разные id
{
  const base = ["kpi_001"];
  const a = nextPrefixedUniqueId("kpi", base);
  const b = nextPrefixedUniqueId("kpi", base);
  assert(a !== b, "concurrent unique");
}

// Orphan option helper (как в picker)
function orphanMetricOptionHtml(sourceMetricId, filteredIds) {
  const id = String(sourceMetricId || "");
  if (!id) return "";
  if (filteredIds.indexOf(id) !== -1) return "";
  return '<option value="' + id + '" selected>' + id + " (больше не в каталоге)</option>";
}
{
  assert(orphanMetricOptionHtml("m_gone", ["m_a"]).indexOf("больше не в каталоге") !== -1, "orphan");
  assert(orphanMetricOptionHtml("m_a", ["m_a"]) === "", "present no orphan");
  assert(orphanMetricOptionHtml("", ["m_a"]) === "", "empty");
}

console.log("wt_package_b_id_integrity_harness: OK");
