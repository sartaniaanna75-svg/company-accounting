/**
 * Swap sort_order среди показателей одного узла (зеркало spuOrgMoveIndicatorSibling).
 * Без app.html / БД.
 */
"use strict";

function listForNode(indicators, nodeId) {
  return indicators.filter(function (it) {
    if (!it || it.is_active === false || it.is_deleted) return false;
    const ids = [];
    if (it.target_org_node_id) ids.push(String(it.target_org_node_id));
    (it.target_org_node_ids || []).forEach(function (id) {
      if (id && ids.indexOf(String(id)) === -1) ids.push(String(id));
    });
    return ids.indexOf(String(nodeId)) !== -1;
  }).sort(function (a, b) {
    return Number(a.sort_order || 0) - Number(b.sort_order || 0);
  });
}

function moveSibling(indicators, nodeId, indicatorId, dir) {
  const list = listForNode(indicators, nodeId);
  const idx = list.findIndex(function (it) { return it.indicator_id === indicatorId; });
  const j = idx + (dir < 0 ? -1 : 1);
  if (idx < 0 || j < 0 || j >= list.length) return false;
  const a = list[idx];
  const b = list[j];
  const ao = Number(a.sort_order || 0);
  const bo = Number(b.sort_order || 0);
  if (ao === bo) a.sort_order = dir < 0 ? bo - 1 : bo + 1;
  else {
    a.sort_order = bo;
    b.sort_order = ao;
  }
  return true;
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const node = "opt";
const inds = [
  { indicator_id: "a", name: "A", target_org_node_id: node, target_org_node_ids: [node], sort_order: 10, source_type: "manual" },
  { indicator_id: "b", name: "B", target_org_node_id: node, target_org_node_ids: [node], sort_order: 20, source_type: "calculated" },
  { indicator_id: "c", name: "C", target_org_node_id: node, target_org_node_ids: [node], sort_order: 30, show_in_planfact: false, source_type: "worktime_metric" },
  { indicator_id: "x", name: "X", target_org_node_id: "other", target_org_node_ids: ["other"], sort_order: 5 },
  { indicator_id: "arch", name: "Arch", target_org_node_id: node, is_active: false, sort_order: 1 },
];

let names = listForNode(inds, node).map(function (it) { return it.name; });
assert(names.join(",") === "A,B,C", "active assigned only; PF flag ignored");
assert(moveSibling(inds, node, "b", -1), "move up");
names = listForNode(inds, node).map(function (it) { return it.name; });
assert(names.join(",") === "B,A,C", "order after up");
assert(inds.find(function (x) { return x.indicator_id === "x"; }).sort_order === 5, "other node untouched");

const tied = [
  { indicator_id: "p", target_org_node_id: "n", target_org_node_ids: ["n"], sort_order: 10 },
  { indicator_id: "q", target_org_node_id: "n", target_org_node_ids: ["n"], sort_order: 10 },
];
assert(moveSibling(tied, "n", "q", -1), "tied move");
assert(listForNode(tied, "n")[0].indicator_id === "q", "tied resolves");

console.log("OK wt_spu_indicator_order_harness");
