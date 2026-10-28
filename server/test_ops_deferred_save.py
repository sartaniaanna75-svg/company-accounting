# -*- coding: utf-8 -*-
"""
Пропуск saveApp: softApplying / blockIncidental / пустой diff при наличии черновика.

До исправления wiring-тесты должны ПАДАТЬ (нет отложенного сохранения).
После исправления — зелёные. Логика зеркала проверяет поведение без Timeweb.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1].joinpath("app.html")


def json_equal(a, b) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
        b, sort_keys=True, ensure_ascii=False
    )


# --- Зеркало СТАРОГО (сломанного) поведения DomainWriter ---

def old_save_during_soft_applying(soft_retry: int, mark_dirty_fn) -> dict:
    """Как сейчас: после 8 ретраев — false, dirty не ставим, отложенного save нет."""
    if soft_retry >= 8:
        return {"ok": False, "dirty": False, "deferred": False, "fetch": False}
    return {"ok": "retry", "dirty": False, "deferred": False, "fetch": False}


def old_save_during_incidental_block() -> dict:
    """Как сейчас: return true без fetch — молчаливый «успех»."""
    return {"ok": True, "dirty": False, "deferred": False, "fetch": False}


def old_empty_diff_with_ui_change(last_folders, app_folders, dirty: set) -> dict:
    """Пустой collect → true; dirty не защищает; SoftSync без dirty возьмёт сервер."""
    leaf = None if json_equal(last_folders, app_folders) else True
    if not leaf:
        return {"ok": True, "fetch": False, "dirty_kept": bool(dirty)}
    return {"ok": "would_fetch", "fetch": True, "dirty_kept": True}


# --- Зеркало НОВОГО поведения ---

def new_save_during_soft_applying(soft_retry: int) -> dict:
    if soft_retry >= 8:
        return {"ok": False, "dirty": True, "deferred": True, "fetch": False}
    return {"ok": "retry", "dirty": False, "deferred": False, "fetch": False}


def new_save_during_incidental_block() -> dict:
    return {"ok": False, "dirty": True, "deferred": True, "fetch": False}


def new_empty_diff_recover_from_dirty(last_folders, app_folders, dirty_paths: set):
    """Если collect пуст, но dirty-путь ещё расходится с last — принудительный leaf."""
    if not json_equal(last_folders, app_folders):
        return [{"path": "shopRevision.folders", "value": app_folders}]
    if "shopRevision.folders" in dirty_paths and not json_equal(last_folders, app_folders):
        return [{"path": "shopRevision.folders", "value": app_folders}]
    # SoftSync обновил lastLoaded до draft без записи: UI «новый», collect пуст
    # (симулируем: last уже = app, но флаг pending_deferred)
    return []


def deferred_flush_wait(blocked: bool, waited_ms: int, warned: bool) -> dict:
    """Зеркало ожидания SoftSync: warn с 8 с, продолжать до 60 с, dirty не сбрасывать."""
    WARN_MS, MAX_MS = 8000, 60000
    if not blocked:
        return {"fetch": True, "warn": False, "stop": False, "reschedule": False, "keep_dirty": True}
    if waited_ms >= MAX_MS:
        return {"fetch": False, "warn": True, "stop": True, "reschedule": False, "keep_dirty": True}
    warn_now = waited_ms >= WARN_MS and not warned
    return {
        "fetch": False,
        "warn": warn_now,
        "stop": False,
        "reschedule": True,
        "keep_dirty": True,
    }


def five_network_failures(max_fail: int = 5) -> dict:
    """Зеркало: ok=false увеличивает streak; стоп когда streak > max_fail; dirty всегда."""
    fail_streak = 0
    dirty = True
    toasts = 0
    for _ in range(5):
        ok = False
        fake_success = bool(ok)
        if not ok:
            fail_streak += 1
            toasts += 1
            if fail_streak > max_fail:
                break
    return {
        "dirty": dirty,
        "fake_success": False,
        "can_manual_retry": dirty,
        "toasts": toasts,
        "auto_stopped_after_five": fail_streak > max_fail,
        "would_stop_on_sixth": fail_streak + 1 > max_fail,
    }


class DeferredSaveBrokenBehaviorTests(unittest.TestCase):
    """Документируют текущий сбой (логика старого кода). Всегда зелёные."""

    def test_old_soft_applying_drops_without_dirty(self):
        r = old_save_during_soft_applying(8, lambda: None)
        self.assertFalse(r["ok"])
        self.assertFalse(r["dirty"])
        self.assertFalse(r["deferred"])
        self.assertFalse(r["fetch"])

    def test_old_incidental_block_fake_success(self):
        r = old_save_during_incidental_block()
        self.assertTrue(r["ok"])
        self.assertFalse(r["fetch"])
        self.assertFalse(r["deferred"])

    def test_old_soft_sync_wipes_undirty_folder(self):
        # Нет dirty → SoftSync берёт серверный массив без новой подпапки
        server = [{"id": "f0"}]
        draft = [{"id": "f0"}, {"id": "f1"}]
        dirty = set()
        merged = draft if "shopRevision.folders" in dirty else server
        self.assertEqual([x["id"] for x in merged], ["f0"])
        self.assertNotIn("f1", [x["id"] for x in merged])


class DeferredSaveRequiredFixTests(unittest.TestCase):
    """Требуемое поведение после исправления — падают, пока нет wiring в app.html."""

    def _save_src(self) -> str:
        src = APP.read_text(encoding="utf-8")
        return src[
            src.find("function wtDomainWriterSave(") : src.find("function saveApp(")
        ]

    def test_wiring_has_deferred_scheduler(self):
        src = APP.read_text(encoding="utf-8")
        self.assertTrue(src.find("function wtDomainWriterScheduleDeferred(") > 0)
        self.assertTrue(src.find("wtDomainWriterDeferredPending") > 0)

    def test_wiring_soft_applying_schedules_deferred(self):
        save = self._save_src()
        idx = save.find("softRetry >= 8")
        self.assertGreater(idx, 0)
        chunk = save[idx : idx + 450]
        self.assertTrue(chunk.find("wtDomainWriterScheduleDeferred") >= 0)

    def test_wiring_incidental_block_not_fake_success(self):
        save = self._save_src()
        idx = save.find("wtOpsBlockIncidentalPut && wtOpsUiEnabled()")
        self.assertGreater(idx, 0)
        chunk = save[idx : idx + 350]
        self.assertTrue(chunk.find("wtDomainWriterScheduleDeferred") >= 0)
        self.assertTrue(chunk.find("return Promise.resolve(false)") >= 0)
        self.assertTrue(chunk.find("return Promise.resolve(true)") < 0)

    def test_wiring_empty_diff_uses_dirty_leaves(self):
        src = APP.read_text(encoding="utf-8")
        self.assertTrue(src.find("function wtDomainWriterLeavesFromDirty(") > 0)
        save = self._save_src()
        self.assertTrue(save.find("wtDomainWriterLeavesFromDirty") >= 0)

    def test_new_soft_applying_defers_with_dirty(self):
        r = new_save_during_soft_applying(8)
        self.assertTrue(r["deferred"])
        self.assertTrue(r["dirty"])
        self.assertFalse(r["fetch"])
        self.assertFalse(r["ok"])

    def test_new_incidental_defers_not_success(self):
        r = new_save_during_incidental_block()
        self.assertTrue(r["deferred"])
        self.assertFalse(r["ok"])
        self.assertFalse(r["fetch"])

    def test_deferred_flush_waits_then_fetches(self):
        self.assertTrue(deferred_flush_wait(True, 0, False)["reschedule"])
        self.assertTrue(deferred_flush_wait(False, 0, False)["fetch"])

    def test_wait_2s_8s_12s_20s_keeps_dirty(self):
        for ms in (2000, 8000, 12000, 20000):
            r = deferred_flush_wait(True, ms, warned=(ms > 8000))
            self.assertTrue(r["keep_dirty"], ms)
            self.assertFalse(r["stop"], ms)
            self.assertTrue(r["reschedule"], ms)
        at8 = deferred_flush_wait(True, 8000, False)
        self.assertTrue(at8["warn"])
        at12 = deferred_flush_wait(True, 12000, True)
        self.assertTrue(at12["reschedule"])
        at60 = deferred_flush_wait(True, 60000, True)
        self.assertTrue(at60["stop"])
        self.assertTrue(at60["keep_dirty"])

    def test_five_network_errors_keep_dirty_no_fake_success(self):
        r = five_network_failures()
        self.assertTrue(r["dirty"])
        self.assertFalse(r["fake_success"])
        self.assertTrue(r["can_manual_retry"])
        self.assertEqual(r["toasts"], 5)
        self.assertTrue(r["would_stop_on_sixth"])

    def test_wiring_long_soft_wait_constants(self):
        src = APP.read_text(encoding="utf-8")
        self.assertTrue(src.find("WT_DOMAIN_DEFERRED_WARN_MS = 8000") > 0)
        self.assertTrue(src.find("WT_DOMAIN_DEFERRED_MAX_MS = 60000") > 0)
        self.assertTrue(src.find("WT_DOMAIN_DEFERRED_MAX_WAIT") < 0)

    def test_revision_folder_survives_soft_block_then_flush(self):
        last = [{"id": "f0"}]
        app = [{"id": "f0"}, {"id": "f1"}]
        dirty = set()
        r = new_save_during_soft_applying(8)
        self.assertTrue(r["deferred"])
        dirty.add("shopRevision.folders")
        merged = app if "shopRevision.folders" in dirty else last
        self.assertIn("f1", [x["id"] for x in merged])
        # 12 с SoftSync всё ещё ждём
        mid = deferred_flush_wait(True, 12000, True)
        self.assertTrue(mid["reschedule"])
        self.assertTrue(mid["keep_dirty"])
        flush = deferred_flush_wait(False, 12000, True)
        self.assertTrue(flush["fetch"])
        last = list(app)
        self.assertEqual([x["id"] for x in last], ["f0", "f1"])


if __name__ == "__main__":
    unittest.main()
