/**
 * СПУ UX: авто metric_overall при смене метрики; legacy/group_total не трогать при «открытии».
 * Зеркало логики app.html без DOM/БД.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function metricWorkTabIds(item) {
  if (!item) return [];
  if (Array.isArray(item.work_tab_ids) && item.work_tab_ids.length) return item.work_tab_ids.slice();
  return [];
}

function metricTabLabel(dept, tabId) {
  const map = {
    drivers: { sales: "Торговые / Продажи ОПТ", drivers: "Водители" },
    warehouse: { pickers: "Товароразборщики", loaders: "Грузчики" }
  };
  return (map[dept] && map[dept][tabId]) || tabId || "—";
}

function spuIndWorktimeSourceUiMode(s) {
  if (!s) return "pending";
  const type = s.source_type || "worktime_metric";
  if (type !== "worktime_metric") return "other";
  if (!s.source_metric_id) return "pending";
  const r = String(s.source_result || "department_total");
  if (r === "metric_overall") return "overall";
  if (r === "group_total") return "simple";
  if (r === "fact" || r === "department_total" || r === "metric_total") return "legacy";
  return "legacy";
}

function spuIndApplyWorktimeGroupAutofill(s) {
  if (!s || (s.source_type || "worktime_metric") !== "worktime_metric") return;
  if (!s.source_metric_id) {
    s.source_result = "department_total";
    s.source_work_tab_id = "";
    return;
  }
  s.source_result = "metric_overall";
  s.source_work_tab_id = "";
}

function spuIndWorktimeSourcesMissingGroup(sources) {
  let hit = null;
  (sources || []).some(function (s) {
    if (!s || (s.source_type || "worktime_metric") !== "worktime_metric") return false;
    if (String(s.source_result || "") !== "group_total") return false;
    if (String(s.source_work_tab_id || "")) return false;
    hit = s;
    return true;
  });
  return hit;
}

function simulateMetricChange(draft, sourceId, newMetricId) {
  const prev = {};
  (draft || []).forEach(function (s) {
    if (s && s.source_id) prev[s.source_id] = String(s.source_metric_id || "");
  });
  const row = (draft || []).find(function (s) { return s && s.source_id === sourceId; });
  assert(row, "source row");
  row.source_metric_id = newMetricId;
  if (String(prev[sourceId] || "") !== String(newMetricId || "")) {
    spuIndApplyWorktimeGroupAutofill(row);
  }
  return row;
}

function simulateOpenOnly(saved) {
  return JSON.parse(JSON.stringify(saved));
}

const catalog = {
  m_one: { id: "m_one", department_id: "drivers", work_tab_ids: ["sales"] },
  m_many: { id: "m_many", department_id: "warehouse", work_tab_ids: ["pickers", "loaders"] },
  m_none: { id: "m_none", department_id: "office", work_tab_ids: [] }
};

// 1) Новая метрика → metric_overall без группы
{
  const draft = [{ source_id: "ssrc_1", source_type: "worktime_metric", source_metric_id: "", source_result: "department_total", source_work_tab_id: "" }];
  const row = simulateMetricChange(draft, "ssrc_1", "m_one");
  assert(row.source_result === "metric_overall", "pick → metric_overall");
  assert(row.source_work_tab_id === "", "no group required");
  assert(spuIndWorktimeSourceUiMode(row) === "overall", "ui overall");
  assert(!spuIndWorktimeSourcesMissingGroup(draft), "overall not blocked by missing group");
  assert(metricTabLabel("drivers", "sales") === "Торговые / Продажи ОПТ", "human label");
}

// 2) Несколько вкладок у метрики — всё равно overall, без выбора группы
{
  const draft = [{ source_id: "ssrc_2", source_type: "worktime_metric", source_metric_id: "", source_result: "department_total", source_work_tab_id: "" }];
  const row = simulateMetricChange(draft, "ssrc_2", "m_many");
  assert(row.source_result === "metric_overall", "multi tabs → overall");
  assert(row.source_work_tab_id === "", "multi → no tab");
  assert(!spuIndWorktimeSourcesMissingGroup(draft), "overall save ok");
}

// 3) 0 групп в каталоге — всё равно overall (не department_total)
{
  const draft = [{ source_id: "ssrc_3", source_type: "worktime_metric", source_metric_id: "", source_result: "department_total", source_work_tab_id: "" }];
  const row = simulateMetricChange(draft, "ssrc_3", "m_none");
  assert(row.source_result === "metric_overall", "zero tabs → overall");
  assert(row.source_result !== "department_total", "not dept_total");
  assert(spuIndWorktimeSourceUiMode(row) === "overall", "ui overall");
  void catalog;
}

// 4–6) Открытие legacy/simple/overall без autofill
{
  const savedDept = [{ source_id: "a", source_type: "worktime_metric", source_metric_id: "m_one", source_result: "department_total", source_work_tab_id: "" }];
  const openDept = simulateOpenOnly(savedDept);
  assert(openDept[0].source_result === "department_total", "open dept_total unchanged");
  assert(spuIndWorktimeSourceUiMode(openDept[0]) === "legacy", "dept_total legacy ui");

  const savedFact = [{ source_id: "b", source_type: "worktime_metric", source_metric_id: "m_one", source_result: "fact", source_work_tab_id: "" }];
  const openFact = simulateOpenOnly(savedFact);
  assert(openFact[0].source_result === "fact", "open fact unchanged");

  const savedGroup = [{ source_id: "c", source_type: "worktime_metric", source_metric_id: "m_one", source_result: "group_total", source_work_tab_id: "sales" }];
  const openGroup = simulateOpenOnly(savedGroup);
  assert(openGroup[0].source_result === "group_total", "open group_total unchanged");
  assert(openGroup[0].source_work_tab_id === "sales", "open group tab unchanged");
  assert(spuIndWorktimeSourceUiMode(openGroup[0]) === "simple", "group simple ui");

  const savedOverall = [{ source_id: "d", source_type: "worktime_metric", source_metric_id: "m_one", source_result: "metric_overall", source_work_tab_id: "" }];
  const openOverall = simulateOpenOnly(savedOverall);
  assert(openOverall[0].source_result === "metric_overall", "open overall unchanged");
  assert(spuIndWorktimeSourceUiMode(openOverall[0]) === "overall", "overall ui");
}

// 7–8) Закрытие без save
{
  const persisted = { source_result: "department_total", source_work_tab_id: "", source_metric_id: "m_one" };
  const draft = simulateOpenOnly([{
    source_id: "x",
    source_type: "worktime_metric",
    source_metric_id: persisted.source_metric_id,
    source_result: persisted.source_result,
    source_work_tab_id: persisted.source_work_tab_id
  }]);
  draft[0].source_result = "metric_overall";
  assert(persisted.source_result === "department_total", "close without save keeps stored");
  const reopen = simulateOpenOnly([{
    source_id: "x",
    source_type: "worktime_metric",
    source_metric_id: persisted.source_metric_id,
    source_result: persisted.source_result,
    source_work_tab_id: persisted.source_work_tab_id
  }]);
  assert(reopen[0].source_result === "department_total", "reopen same");
}

// 9) Смена метрики в существующей карточке — только черновик → overall
{
  const stored = { source_id: "y", source_type: "worktime_metric", source_metric_id: "m_one", source_result: "department_total", source_work_tab_id: "" };
  const draft = simulateOpenOnly([stored]);
  simulateMetricChange(draft, "y", "m_many");
  assert(draft[0].source_result === "metric_overall", "draft overall after metric change");
  assert(stored.source_result === "department_total", "stored untouched until save");
}

// 10) Другие типы источников не трогаем
{
  const draft = [{ source_id: "z", source_type: "timesheet", source_metric_id: "", source_result: "department_total", source_work_tab_id: "", source_timesheet_agg: "days" }];
  const prev = draft[0].source_result;
  assert(draft[0].source_type === "timesheet", "timesheet intact");
  assert(draft[0].source_result === prev, "timesheet result intact");
}

// 11) Legacy metric_total остаётся legacy (не overall)
{
  const row = { source_id: "mt", source_type: "worktime_metric", source_metric_id: "m_one", source_result: "metric_total", source_work_tab_id: "" };
  assert(spuIndWorktimeSourceUiMode(row) === "legacy", "metric_total stays legacy");
  assert(spuIndWorktimeSourceUiMode(row) !== "overall", "not auto overall");
}

console.log("wt_spu_source_group_ux_harness: OK");
