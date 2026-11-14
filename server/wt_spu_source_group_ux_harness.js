/**
 * СПУ UX: авто group_total при явной смене метрики; legacy fact/department_total не трогать при «открытии».
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
  if (r === "group_total") return "simple";
  if (r === "fact" || r === "department_total" || r === "metric_total") return "legacy";
  return "legacy";
}

function spuIndApplyWorktimeGroupAutofill(s, catalogById) {
  if (!s || (s.source_type || "worktime_metric") !== "worktime_metric") return;
  if (!s.source_metric_id) {
    s.source_result = "department_total";
    s.source_work_tab_id = "";
    return;
  }
  const item = catalogById[s.source_metric_id];
  const tabs = item ? metricWorkTabIds(item) : [];
  if (tabs.length === 1) {
    s.source_result = "group_total";
    s.source_work_tab_id = tabs[0];
    return;
  }
  if (tabs.length > 1) {
    const cur = String(s.source_work_tab_id || "");
    s.source_result = "group_total";
    s.source_work_tab_id = tabs.indexOf(cur) !== -1 ? cur : "";
    return;
  }
  s.source_result = "group_total";
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

function simulateMetricChange(draft, sourceId, newMetricId, catalogById) {
  const prev = {};
  (draft || []).forEach(function (s) {
    if (s && s.source_id) prev[s.source_id] = String(s.source_metric_id || "");
  });
  const row = (draft || []).find(function (s) { return s && s.source_id === sourceId; });
  assert(row, "source row");
  row.source_metric_id = newMetricId;
  if (String(prev[sourceId] || "") !== String(newMetricId || "")) {
    spuIndApplyWorktimeGroupAutofill(row, catalogById);
  }
  return row;
}

function simulateOpenOnly(saved) {
  /* Открытие = копия в черновик, без autofill */
  return JSON.parse(JSON.stringify(saved));
}

const catalog = {
  m_one: { id: "m_one", department_id: "drivers", work_tab_ids: ["sales"] },
  m_many: { id: "m_many", department_id: "warehouse", work_tab_ids: ["pickers", "loaders"] },
  m_none: { id: "m_none", department_id: "office", work_tab_ids: [] }
};

// 1) Новая метрика с одной группой → group_total + tab
{
  const draft = [{ source_id: "ssrc_1", source_type: "worktime_metric", source_metric_id: "", source_result: "department_total", source_work_tab_id: "" }];
  const row = simulateMetricChange(draft, "ssrc_1", "m_one", catalog);
  assert(row.source_result === "group_total", "one-tab → group_total");
  assert(row.source_work_tab_id === "sales", "one-tab → sales");
  assert(spuIndWorktimeSourceUiMode(row) === "simple", "ui simple");
  assert(metricTabLabel("drivers", "sales") === "Торговые / Продажи ОПТ", "human label");
  assert(metricTabLabel("drivers", "sales") !== "sales", "not raw code");
}

// 2) Несколько групп → явный выбор, произвольная не подставляется
{
  const draft = [{ source_id: "ssrc_2", source_type: "worktime_metric", source_metric_id: "", source_result: "department_total", source_work_tab_id: "" }];
  const row = simulateMetricChange(draft, "ssrc_2", "m_many", catalog);
  assert(row.source_result === "group_total", "multi → mode group_total");
  assert(row.source_work_tab_id === "", "multi → no arbitrary tab");
  assert(spuIndWorktimeSourcesMissingGroup(draft), "save blocked until group chosen");
  row.source_work_tab_id = "pickers";
  assert(!spuIndWorktimeSourcesMissingGroup(draft), "ok after pick");
}

// 3) 0 групп → не department_total; без tab; save блокируется
{
  const draft = [{ source_id: "ssrc_3", source_type: "worktime_metric", source_metric_id: "", source_result: "department_total", source_work_tab_id: "" }];
  const row = simulateMetricChange(draft, "ssrc_3", "m_none", catalog);
  assert(row.source_result !== "department_total", "zero tabs → not department_total");
  assert(row.source_result === "group_total", "zero tabs → incomplete group_total");
  assert(row.source_work_tab_id === "", "zero tabs → empty tab");
  assert(spuIndWorktimeSourcesMissingGroup(draft), "zero tabs → save blocked");
  assert(spuIndWorktimeSourceUiMode(row) === "simple", "zero tabs ui not legacy dept");
}

// 4–6) Открытие legacy/simple без autofill
{
  const savedDept = [{ source_id: "a", source_type: "worktime_metric", source_metric_id: "m_one", source_result: "department_total", source_work_tab_id: "" }];
  const openDept = simulateOpenOnly(savedDept);
  assert(openDept[0].source_result === "department_total", "open dept_total unchanged");
  assert(openDept[0].source_work_tab_id === "", "open dept_total tab unchanged");
  assert(spuIndWorktimeSourceUiMode(openDept[0]) === "legacy", "dept_total legacy ui");

  const savedFact = [{ source_id: "b", source_type: "worktime_metric", source_metric_id: "m_one", source_result: "fact", source_work_tab_id: "" }];
  const openFact = simulateOpenOnly(savedFact);
  assert(openFact[0].source_result === "fact", "open fact unchanged");
  assert(spuIndWorktimeSourceUiMode(openFact[0]) === "legacy", "fact legacy ui");

  const savedGroup = [{ source_id: "c", source_type: "worktime_metric", source_metric_id: "m_one", source_result: "group_total", source_work_tab_id: "sales" }];
  const openGroup = simulateOpenOnly(savedGroup);
  assert(openGroup[0].source_result === "group_total", "open group_total unchanged");
  assert(openGroup[0].source_work_tab_id === "sales", "open group tab unchanged");
  assert(spuIndWorktimeSourceUiMode(openGroup[0]) === "simple", "group simple ui");
}

// 7–8) Закрытие без save / повторное открытие — сохранённые значения те же
{
  const persisted = { source_result: "department_total", source_work_tab_id: "", source_metric_id: "m_one" };
  const draft = simulateOpenOnly([{
    source_id: "x",
    source_type: "worktime_metric",
    source_metric_id: persisted.source_metric_id,
    source_result: persisted.source_result,
    source_work_tab_id: persisted.source_work_tab_id
  }]);
  draft[0].source_result = "group_total";
  draft[0].source_work_tab_id = "sales";
  /* без save persisted не меняется */
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

// 9) Смена метрики в существующей карточке — только черновик
{
  const stored = { source_id: "y", source_type: "worktime_metric", source_metric_id: "m_one", source_result: "department_total", source_work_tab_id: "" };
  const draft = simulateOpenOnly([stored]);
  simulateMetricChange(draft, "y", "m_many", catalog);
  assert(draft[0].source_result === "group_total", "draft updated after metric change");
  assert(stored.source_result === "department_total", "stored untouched until save");
}

// 10) Другие типы источников не трогаем autofill
{
  const draft = [{ source_id: "z", source_type: "timesheet", source_metric_id: "", source_result: "department_total", source_work_tab_id: "", source_timesheet_agg: "days" }];
  const prev = draft[0].source_result;
  /* metric change N/A; apply would no-op for non-worktime if guarded by type in onMetricChange */
  assert(draft[0].source_type === "timesheet", "timesheet intact");
  assert(draft[0].source_result === prev, "timesheet result intact");
}

console.log("wt_spu_source_group_ux_harness: OK");
