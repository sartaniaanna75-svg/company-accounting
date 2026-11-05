/**
 * Контракт: галочка KPI → только узкий OPS shopPayroll.assignments (1 leaf),
 * даже если «чужой» collect дал бы 496 листьев. Без saveApp / forcePut.
 */
function assert(c, m) { if (!c) throw new Error(m); }

function buildAssignOp(assignments) {
  return {
    path: "shopPayroll.assignments",
    value: assignments,
    expected_v: 0
  };
}

/** Как DomainWriter: явные ops не смешиваются с полным collect. */
function leavesForSave(explicitOps, fullCollectCount) {
  if (explicitOps && explicitOps.length) return explicitOps.slice();
  return new Array(fullCollectCount).fill(0).map(function (_, i) {
    return { path: "noise." + i, value: i };
  });
}

function wouldRejectOver200(leaves) {
  return !!(leaves && leaves.length > 200);
}

function noOpsResult(opsUiEnabled) {
  if (!opsUiEnabled) return false; // отказ, не saveApp
  return "ops";
}

const assignOp = buildAssignOp([{ id: "a1", employee_id: "e1", kpi_id: "k1", is_active: true }]);
assert(assignOp.path === "shopPayroll.assignments", "path assignments");

const withNoise = leavesForSave([assignOp], 496);
assert(withNoise.length === 1, "explicit ops ignore 496 noise");
assert(wouldRejectOver200(withNoise) === false, "1 leaf under 200");

const fullCollect = leavesForSave(null, 496);
assert(fullCollect.length === 496, "full collect 496");
assert(wouldRejectOver200(fullCollect) === true, "496 rejected");

assert(noOpsResult(false) === false, "OPS off -> refuse, no saveApp");
assert(noOpsResult(true) === "ops", "OPS on -> narrow path");

console.log("OK wt_kpi_assign_save_harness");
