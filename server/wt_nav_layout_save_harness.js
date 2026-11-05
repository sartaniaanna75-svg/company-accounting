/**
 * Контракт saveNavLayoutEditor: успех только при ok===true; путь только navLayouts.<key>.
 */
function buildOp(key, layout) {
  return {
    path: "navLayouts." + key,
    value: layout,
    expected_v: 0
  };
}

function shouldToastSuccess(ok) {
  return ok === true;
}

/** Контракт: без OPS — false, без saveApp/forcePut. */
function noOpsResult(opsUiEnabled) {
  if (!opsUiEnabled) return false;
  return "ops";
}

function assert(c, m) { if (!c) throw new Error(m); }

const op = buildOp("summary", { saved: true, groups: [{ group_id: "ng1", title: "A", item_ids: ["x"] }] });
assert(op.path === "navLayouts.summary", "path scoped to section");
assert(op.path.indexOf("workspaces") === -1, "no workspaces in op");
assert(shouldToastSuccess(true) === true, "true -> success");
assert(shouldToastSuccess(false) === false, "false -> no success");
assert(shouldToastSuccess({ wtConflicts: [] }) === false, "conflict object -> no success");
assert(noOpsResult(false) === false, "OPS off -> refuse");
assert(noOpsResult(true) === "ops", "OPS on -> allow ops path");

console.log("OK wt_nav_layout_save_harness");
