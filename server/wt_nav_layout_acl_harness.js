/**
 * Контракт ACL меню: запись navLayouts только системному администратору.
 * Зеркало серверного ops_path_allowed(top==navLayouts) без live БД.
 */
function isAppAdmin(user) {
  if (!user || user.is_active === false) return false;
  return user.role === "admin" || user.legacy_role === "admin";
}

function userHasPerm(user, key, min) {
  const v = (user && user.permissions && user.permissions[key]) || "none";
  if (min === "edit") return v === "edit" || v === "manage";
  return v !== "none";
}

/** Как wt_api.ops_path_allowed для top=navLayouts (после правки). */
function navLayoutsOpAllowed(user) {
  if (!user) return false;
  if (isAppAdmin(user)) return true;
  // settings:edit больше не открывает navLayouts
  return isAppAdmin(user);
}

function assert(c, m) { if (!c) throw new Error(m); }

const admin = { role: "admin", legacy_role: "admin", permissions: { settings: "edit" } };
const settingsStaff = { role: "user", legacy_role: "staff", permissions: { settings: "edit" } };
const plainStaff = { role: "user", permissions: { timesheet: "edit" } };

assert(navLayoutsOpAllowed(admin) === true, "admin can write navLayouts");
assert(navLayoutsOpAllowed(settingsStaff) === false, "settings:edit cannot write navLayouts");
assert(navLayoutsOpAllowed(plainStaff) === false, "staff cannot write navLayouts");
assert(userHasPerm(settingsStaff, "settings", "edit") === true, "settings:edit still true for other features");
assert(isAppAdmin(settingsStaff) === false, "settings user is not app admin");

console.log("OK wt_nav_layout_acl_harness");
