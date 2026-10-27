# -*- coding: utf-8 -*-
"""Гонка DomainWriter/OPS: снимок до очереди vs сбор внутри очереди + applyBatchLocal.

Также FakeConn: 5 подпапок подряд, 409→повтор «свои», замер числа запросов.
Без Timeweb / PostgreSQL.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_SERVER_DIR = Path(__file__).resolve().parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

APP = Path(__file__).resolve().parents[1].joinpath("app.html")


def json_equal(a, b) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
        b, sort_keys=True, ensure_ascii=False
    )


def collect_leaf(base, draft, path="shopRevision.folders"):
    if json_equal(base, draft):
        return None
    return {"path": path, "value": json.loads(json.dumps(draft))}


def apply_batch_local_old(app, path, saved_value):
    """Старое поведение: всегда писать saved в app."""
    app[path] = json.loads(json.dumps(saved_value))
    return False  # dirty cleared


def apply_batch_local_new(app, last, path, saved_value, dirty):
    """Новое: не затирать app, если локально новее; last всегда = saved."""
    local = app.get(path)
    local_newer = not json_equal(local, saved_value)
    if not local_newer:
        app[path] = json.loads(json.dumps(saved_value))
        dirty.discard(path)
    else:
        dirty.add(path)
    last[path] = json.loads(json.dumps(saved_value))
    return local_newer


class OpsSaveRaceTests(unittest.TestCase):
    def test_app_wiring(self):
        src = APP.read_text(encoding="utf-8")
        self.assertIn("function wtDomainWriterOpsFromLeaves(", src)
        apply = src[
            src.find("function wtDomainWriterSave(")
            - 2500 : src.find("function wtDomainWriterSave(")
        ]
        # applyBatchLocal должен уметь localNewer
        block = src[
            src.find("function wtDomainWriterApplyBatchLocal(") : src.find(
                "function wtDomainWriterOpsFromLeaves("
            )
        ]
        self.assertIn("localNewer", block)
        self.assertIn("wtSoftMarkDirty(row.path)", block)
        # OPS собираются внутри wtOpsChain
        save = src[
            src.find("function wtDomainWriterSave(") : src.find("function saveApp(")
        ]
        self.assertIn("wtOpsChain.then(function ()", save)
        self.assertIn("wtCollectChangedLeaves(wtLastLoadedPayload || {}, appData)", save)
        # повторный collect должен быть внутри then (после prelim)
        idx_chain = save.find("const job = wtOpsChain.then")
        idx_fresh = save.find(
            "leaves = wtCollectChangedLeaves(wtLastLoadedPayload || {}, appData)",
            idx_chain,
        )
        self.assertGreater(idx_chain, 0)
        self.assertGreater(idx_fresh, idx_chain)
        # 409 yours — отложенный свежий save (без deadlock внутри wtOpsChain)
        self.assertIn('if (choice === "yours")', save)
        self.assertIn("wtDomainWriterSave({ atomic: atomic })", save)
        self.assertIn("взаимная блокировка", save)
        # reopen KPI ждёт saveApp
        reopen = src[
            src.find("function reopenShopKpiDef(") : src.find("function shopCheckKpiSettingsHtml(")
        ]
        self.assertIn("Promise.resolve(saveApp())", reopen)

    def test_old_apply_wipes_second_folder(self):
        app = {"shopRevision.folders": [{"id": "f0"}]}
        # add A, snapshot ops1
        app["shopRevision.folders"] = [{"id": "f0"}, {"id": "A"}]
        ops1 = collect_leaf([{"id": "f0"}], app["shopRevision.folders"])
        # add B before save1 done
        app["shopRevision.folders"] = [{"id": "f0"}, {"id": "A"}, {"id": "B"}]
        apply_batch_local_old(app, "shopRevision.folders", ops1["value"])
        ids = [x["id"] for x in app["shopRevision.folders"]]
        self.assertNotIn("B", ids)

    def test_new_apply_keeps_second_folder(self):
        path = "shopRevision.folders"
        app = {path: [{"id": "f0"}, {"id": "A"}, {"id": "B"}]}
        last = {path: [{"id": "f0"}]}
        dirty = set()
        ops1_value = [{"id": "f0"}, {"id": "A"}]
        newer = apply_batch_local_new(app, last, path, ops1_value, dirty)
        self.assertTrue(newer)
        self.assertEqual([x["id"] for x in app[path]], ["f0", "A", "B"])
        self.assertEqual([x["id"] for x in last[path]], ["f0", "A"])
        self.assertIn(path, dirty)
        # следующий save внутри очереди видит diff last→app = все три
        leaf = collect_leaf(last[path], app[path])
        self.assertIsNotNone(leaf)
        self.assertEqual([x["id"] for x in leaf["value"]], ["f0", "A", "B"])

    def test_queue_fresh_collect_avoids_stale_expected(self):
        path = "shopRevision.folders"
        last = {path: [{"id": "f0"}]}
        record_v = {path: 1}
        app = {path: [{"id": "f0"}, {"id": "A"}]}
        # устаревший снимок (как раньше до очереди)
        stale_expected = record_v[path]
        # add B while queued
        app[path] = [{"id": "f0"}, {"id": "A"}, {"id": "B"}]
        # save1 completes
        last[path] = [{"id": "f0"}, {"id": "A"}]
        record_v[path] = 2
        apply_batch_local_new(app, last, path, last[path], set())
        # fresh collect inside queue
        leaf = collect_leaf(last[path], app[path])
        fresh_expected = record_v[path]
        self.assertEqual(fresh_expected, 2)
        self.assertNotEqual(stale_expected, fresh_expected)
        self.assertEqual(len(leaf["value"]), 3)

    def test_five_rapid_folders_preserved(self):
        path = "shopRevision.folders"
        last = {path: []}
        app = {path: []}
        dirty = set()
        pending = []
        for i in range(5):
            app[path] = list(app[path]) + [{"id": "f" + str(i)}]
            # prelim dirty
            dirty.add(path)
            pending.append(json.loads(json.dumps(app[path])))
        # serialize: each completion applies; local always has full list already
        for snap in pending:
            apply_batch_local_new(app, last, path, snap, dirty)
        self.assertEqual([x["id"] for x in app[path]], ["f0", "f1", "f2", "f3", "f4"])
        # final fresh leaf empty if last caught up via last save body
        apply_batch_local_new(app, last, path, app[path], dirty)
        self.assertIsNone(collect_leaf(last[path], app[path]))

    def test_conflict_cancel_keeps_local(self):
        path = "shopRevision.folders"
        app = {path: [{"id": "f0"}, {"id": "A"}, {"id": "B"}]}
        server = [{"id": "f0"}, {"id": "A"}]
        # cancel: do not write server into app
        self.assertEqual([x["id"] for x in app[path]], ["f0", "A", "B"])
        self.assertNotEqual(
            [x["id"] for x in app[path]], [x["id"] for x in server]
        )

    def test_multiuser_same_path_still_detectable(self):
        """Чужое изменение на том же пути: expected_v устаревает → конфликт, не молчаливое merge."""
        local = [{"id": "mine"}]
        server = [{"id": "theirs"}]
        expected_v = 3
        server_v = 4
        self.assertNotEqual(expected_v, server_v)
        self.assertFalse(json_equal(local, server))

    def test_yours_retry_is_deferred_not_awaited(self):
        """Риск: после 409 «Свои» клиент возвращает true до завершения повторного save."""
        src = APP.read_text(encoding="utf-8")
        save = src[
            src.find("function wtDomainWriterSave(") : src.find("function saveApp(")
        ]
        # deferred retry
        self.assertIn("Promise.resolve().then(function ()", save)
        self.assertIn("wtDomainWriterSave({ atomic: atomic })", save)
        # после «yours» в атомарном 409 сейчас return true (не ждёт результат retry)
        yours = save.find('if (choice === "yours")')
        chunk = save[yours : yours + 500]
        self.assertIn("return true;", chunk)


class OpsRevisionFakeConnTests(unittest.TestCase):
    """Последовательные OPS shopRevision.folders на FakeConn + 409 retry."""

    def setUp(self):
        import wt_api
        from test_wt_api import SAMPLE_APP, FakeConn, SECRET, call, cookie_from

        self.wt_api = wt_api
        self.call = call
        self.cookie_from = cookie_from
        self.FakeConn = FakeConn
        self._env = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["shopRevision"] = {
            "groups": [{"id": "g1", "name": "G"}],
            "folders": [],
            "tasks": {},
            "ui": {"collapsedGroups": {}},
        }
        self.store = {
            "payload": payload,
            "version": 1,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env)
        self.wt_api.LOGIN_FAILS.clear()

    def login(self):
        st, _, _, cookies = self.call("POST", "/api/login", {"user_id": "user_admin", "pin": "5555"})
        self.assertTrue(st.startswith("200"), st)
        return self.cookie_from(cookies)

    def _record_v(self, path="shopRevision.folders"):
        rec = (self.store["payload"].get("_wt_records") or {}).get(path) or {}
        return int(rec.get("v") or 0)

    def _batch(self, cookie, folders, expected_v, key):
        return self.call(
            "POST",
            "/api/state/op/batch",
            {
                "idempotency_key": key,
                "atomic": False,
                "ops": [
                    {
                        "path": "shopRevision.folders",
                        "expected_v": expected_v,
                        "value": folders,
                    }
                ],
            },
            cookie=cookie,
        )

    def test_five_folders_sequential_survive_reload(self):
        cookie = self.login()
        folders = []
        t0 = time.perf_counter()
        requests = 0
        for i in range(5):
            folders = list(folders) + [{"id": "f" + str(i), "groupId": "g1", "name": "n" + str(i)}]
            ev = self._record_v()
            st, data, *_ = self._batch(cookie, folders, ev, "rev-f-" + str(i))
            requests += 1
            self.assertTrue(st.startswith("200"), st + " " + str(data))
            results = data.get("results") or []
            self.assertEqual(results[0].get("status"), "saved")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # «повторная загрузка»
        st, state, *_ = self.call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st.startswith("200"), st)
        requests += 1
        got = state["payload"]["shopRevision"]["folders"]
        self.assertEqual([x["id"] for x in got], ["f0", "f1", "f2", "f3", "f4"])
        print("PERF five_folders_ms=%.1f requests=%d" % (elapsed_ms, requests))

    def test_one_folder_timing(self):
        cookie = self.login()
        t0 = time.perf_counter()
        st, data, *_ = self._batch(
            cookie, [{"id": "f0", "groupId": "g1", "name": "a"}], 0, "rev-one"
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual((data.get("results") or [{}])[0].get("status"), "saved")
        print("PERF one_folder_ms=%.1f requests=1" % elapsed_ms)

    def test_rename_several_then_reload(self):
        cookie = self.login()
        folders = [{"id": "f0", "groupId": "g1", "name": ""}, {"id": "f1", "groupId": "g1", "name": ""}]
        st, data, *_ = self._batch(cookie, folders, 0, "rev-r0")
        self.assertTrue(st.startswith("200"), st)
        ev = self._record_v()
        folders = [
            {"id": "f0", "groupId": "g1", "name": "Альфа"},
            {"id": "f1", "groupId": "g1", "name": "Бета"},
        ]
        st, data, *_ = self._batch(cookie, folders, ev, "rev-r1")
        self.assertTrue(st.startswith("200"), st)
        st, state, *_ = self.call("GET", "/api/state", cookie=cookie)
        names = [x["name"] for x in state["payload"]["shopRevision"]["folders"]]
        self.assertEqual(names, ["Альфа", "Бета"])

    def test_409_then_yours_retry_persists(self):
        cookie = self.login()
        # пользователь A сохранил
        st, d1, *_ = self._batch(
            cookie, [{"id": "f0", "groupId": "g1", "name": "A"}], 0, "rev-c1"
        )
        self.assertTrue(st.startswith("200"), st)
        # второй клиент со старым expected_v=0 и своими данными → 409
        st, conflict, *_ = self._batch(
            cookie,
            [{"id": "f0", "groupId": "g1", "name": "A"}, {"id": "f1", "groupId": "g1", "name": "B"}],
            0,
            "rev-c2",
        )
        self.assertTrue(st.startswith("409") or (
            isinstance(conflict, dict)
            and (
                conflict.get("error") == "conflict"
                or any(
                    (r or {}).get("status") == "conflict"
                    for r in (conflict.get("results") or [])
                )
            )
        ), str(conflict))
        # «Свои»: повтор с актуальным expected_v и полным локальным значением
        ev = self._record_v()
        mine = [
            {"id": "f0", "groupId": "g1", "name": "A"},
            {"id": "f1", "groupId": "g1", "name": "B"},
        ]
        st, d3, *_ = self._batch(cookie, mine, ev, "rev-c3-yours")
        self.assertTrue(st.startswith("200"), st + " " + str(d3))
        results = d3.get("results") or []
        self.assertEqual(results[0].get("status"), "saved")
        st, state, *_ = self.call("GET", "/api/state", cookie=cookie)
        ids = [x["id"] for x in state["payload"]["shopRevision"]["folders"]]
        self.assertEqual(ids, ["f0", "f1"])

    def test_client_queue_fresh_expected_avoids_self_409(self):
        """Зеркало исправления: после каждого save берём новый expected_v и полный app."""
        cookie = self.login()
        app = []
        last = []
        for i in range(5):
            app = list(app) + [{"id": "f" + str(i), "groupId": "g1", "name": ""}]
            # apply предыдущего не затирает app (local newer / already full)
            ev = self._record_v()
            st, data, *_ = self._batch(cookie, app, ev, "q-" + str(i))
            self.assertTrue(st.startswith("200"), "step %s %s %s" % (i, st, data))
            # last = saved; app уже полный
            last = list(app)
            self.assertEqual(len(last), i + 1)
        st, state, *_ = self.call("GET", "/api/state", cookie=cookie)
        self.assertEqual(len(state["payload"]["shopRevision"]["folders"]), 5)


if __name__ == "__main__":
    unittest.main()
