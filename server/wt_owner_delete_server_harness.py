# -*- coding: utf-8 -*-
"""Unit checks for WT_OWNER_USER_ID without DB / Timeweb."""
from __future__ import annotations

import os
import sys

# server/ on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wt_api  # noqa: E402


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def with_owner(uid: str | None):
    class _Ctx:
        def __enter__(self):
            self.prev = os.environ.get("WT_OWNER_USER_ID")
            if uid is None:
                os.environ.pop("WT_OWNER_USER_ID", None)
            else:
                os.environ["WT_OWNER_USER_ID"] = uid
            return self

        def __exit__(self, *args):
            if self.prev is None:
                os.environ.pop("WT_OWNER_USER_ID", None)
            else:
                os.environ["WT_OWNER_USER_ID"] = self.prev

    return _Ctx()


owner = {"user_id": "user_admin", "role": "admin", "is_active": True}
other_admin = {"user_id": "usr_other", "role": "admin", "is_active": True}
inactive = {"user_id": "user_admin", "role": "admin", "is_active": False}

with with_owner(None):
    assert_true(wt_api.configured_owner_user_id() == "", "empty env")
    assert_true(not wt_api.is_payload_owner(owner), "no owner when unset")
    assert_true(
        wt_api.employee_delete_owner_denied(
            owner,
            {"employeeRemovals": [{"department_id": "office", "employee_id": "E1"}]},
        )
        == "employee_delete_forbidden",
        "unset forbids put removal",
    )

with with_owner("user_admin"):
    assert_true(wt_api.is_payload_owner(owner), "owner match")
    assert_true(not wt_api.is_payload_owner(other_admin), "other admin not owner")
    assert_true(not wt_api.is_payload_owner(inactive), "inactive not owner")
    assert_true(
        wt_api.employee_delete_owner_denied(
            other_admin,
            {"employeeRemovals": [{"department_id": "office", "employee_id": "E1"}]},
        )
        == "employee_delete_forbidden",
        "other admin put denied",
    )
    assert_true(
        wt_api.employee_delete_owner_denied(
            owner,
            {"employeeRemovals": [{"department_id": "office", "employee_id": "E1"}]},
        )
        is None,
        "owner put allowed at owner gate",
    )
    assert_true(
        wt_api.employee_delete_owner_denied(owner, {"workspaces": []}) is None,
        "no removals => no owner gate",
    )
    pub = wt_api.public_session_user(owner)
    assert_true(pub and pub.get("is_owner") is True, "session user is_owner")
    pub2 = wt_api.public_session_user(other_admin)
    assert_true(pub2 and pub2.get("is_owner") is False, "other session not owner")
    plain = wt_api.public_user(owner)
    assert_true(plain is not None and "is_owner" not in plain, "directory public_user no is_owner")

# History still blocks independently of owner
current = {
    "workspaces": [
        {
            "code": "office",
            "employees": [{"id": "E1", "name": "X"}],
            "employeeIds": ["E1"],
            "timesheet": {"2026-10": {"E1": {"1": {"status": "Я", "hours": 8}}}},
        }
    ]
}
assert_true(
    wt_api.employee_removals_denied(
        current,
        {"employeeRemovals": [{"department_id": "office", "employee_id": "E1"}]},
    )
    == "employee_has_history",
    "history still blocks",
)

print("OK wt_owner_delete_server_harness")
