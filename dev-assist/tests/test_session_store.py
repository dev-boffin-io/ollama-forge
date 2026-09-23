"""
Tests for core/session_store.py — the persistent (SQLite) session store.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import session_store as ss
from core.session import get_session, reset_session


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Fresh store in an isolated dir for every test."""
    monkeypatch.setenv("DEV_ASSIST_DATA_DIR", str(tmp_path))
    ss.reset_state()
    reset_session()
    yield ss
    ss.reset_state()
    reset_session()


class TestCreateAndAppend:
    def test_create_is_persisted(self, store):
        s = store.create_session(project="/proj")
        assert s.id
        assert s.title == ""
        assert s.project == "/proj"
        assert store.get_session_info(s.id) == s

    def test_append_auto_titles_from_first_user_message(self, store):
        s = store.create_session()
        store.append_message(s.id, "user", "  Fix the failing build  ")
        info = store.get_session_info(s.id)
        assert info.title == "Fix the failing build"

    def test_empty_append_returns_none_and_sets_no_title(self, store):
        s = store.create_session()
        assert store.append_message(s.id, "user", "   ") is None
        assert store.get_session_info(s.id).title == ""

    def test_append_to_unknown_session_is_safe(self, store):
        assert store.append_message("does-not-exist", "user", "hi") is None

    def test_messages_keep_order_and_content(self, store):
        s = store.create_session()
        store.append_message(s.id, "user", "one")
        store.append_message(s.id, "assistant", "two")
        store.append_message(s.id, "user", "three")
        msgs = store.get_messages(s.id)
        assert [m.content for m in msgs] == ["one", "two", "three"]
        assert [m.role for m in msgs] == ["user", "assistant", "user"]
        assert store.message_count(s.id) == 3

    def test_multiline_content_survives_round_trip(self, store):
        s = store.create_session()
        store.append_message(s.id, "user", "line 1\nline 2\n\tline 3")
        assert store.get_messages(s.id)[0].content == "line 1\nline 2\n\tline 3"

    def test_long_message_does_not_break_title(self, store):
        s = store.create_session()
        store.append_message(s.id, "user", "x" * 5000)
        assert len(store.get_session_info(s.id).title) == 60


class TestListAndResume:
    def test_list_orders_most_recent_first(self, store):
        old = store.create_session(project="/p")
        store.append_message(old.id, "user", "hello")
        new = store.create_session(project="/p")
        store.append_message(new.id, "user", "world")
        sessions = store.list_sessions(project="/p")
        assert [s.id for s in sessions] == [new.id, old.id]

    def test_list_filters_by_project(self, store):
        a = store.create_session(project="/a")
        store.append_message(a.id, "user", "x")
        b = store.create_session(project="/b")
        store.append_message(b.id, "user", "y")
        assert [s.id for s in store.list_sessions(project="/a")] == [a.id]
        assert len(store.list_sessions()) == 2

    def test_latest_session(self, store):
        assert store.latest_session() is None
        store.create_session()
        assert store.latest_session() is not None

    def test_rename_and_delete(self, store):
        s = store.create_session()
        store.append_message(s.id, "user", "hi")
        assert store.rename_session(s.id, "My Work")
        assert store.get_session_info(s.id).title == "My Work"
        assert store.rename_session("missing", "X") is False
        assert store.delete_session(s.id) is True
        assert store.get_session_info(s.id) is None
        assert store.get_messages(s.id) == []
        assert store.delete_session(s.id) is False


class TestActiveSession:
    def test_new_session_loads_empty_context(self, store):
        sess = store.new_session(project="/x")
        assert store.current_session_id() == sess.id
        assert get_session().store_id == sess.id
        assert get_session().get_history() == []

    def test_write_through_persistence(self, store):
        sess = store.new_session()
        from core.session import get_session

        context = get_session()
        context.add_user("hello")
        assert store.message_count(sess.id) == 1
        assert store.get_messages(sess.id)[0].content == "hello"
        context.add_assistant("world")
        assert store.message_count(sess.id) == 2
        assert get_session().store_id == sess.id

    def test_resume_restores_history_and_keeps_writing(self, store):
        s1 = store.new_session()
        from core.session import get_session

        get_session().add_user("first question")
        get_session().add_assistant("first answer")

        # Starting a new session does not disturb the active `get_session()`.
        store.new_session()
        assert get_session().get_history() == []

        restored = store.resume_session(s1.id)
        assert restored is not None
        assert store.current_session_id() == s1.id
        assert get_session().store_id == s1.id
        assert [t.content for t in get_session().get_history()] == [
            "first question", "first answer",
        ]

        # New turns append to the *resumed* session.
        get_session().add_user("follow up")
        stored = store.get_messages(s1.id)
        assert stored[-1].content == "follow up"

    def test_resume_missing_returns_none(self, store):
        assert store.resume_session("missing") is None

    def test_bootstrap_fresh_by_default(self, store):
        first = store.bootstrap(project="/p")
        assert store.current_session_id() == first.id

    def test_bootstrap_is_idempotent_within_process(self, store):
        first = store.bootstrap(project="/p")
        assert store.bootstrap(project="/p").id == first.id

    def test_bootstrap_resume_latest(self, store):
        first = store.bootstrap(project="/p")
        # Simulate a fresh process: state reset, same DB.
        ss.reset_state()
        reset_session()
        second = store.bootstrap(project="/p")
        assert first.id != second.id
        again = store.bootstrap(project="/p", resume=True)
        assert again.id == second.id

    def test_bootstrap_with_id(self, store):
        first = store.bootstrap(project="/p")
        ss.reset_state()
        reset_session()
        store.bootstrap(project="/p")  # starts another
        resumed = store.bootstrap(project="/p", resume=first.id)
        assert resumed.id == first.id


class TestDurability:
    def test_data_survives_database_reconnect(self, store, tmp_path):
        s = store.new_session(project="/persist")
        from core.session import get_session

        get_session().add_user("keep me")
        # Simulate app restart: new process state, same DB file.
        ss.reset_state()
        reset_session()
        stored = store.get_session_info(s.id)
        assert stored is not None
        assert [m.content for m in store.get_messages(s.id)] == ["keep me"]

    def test_bad_content_values_handled(self, store):
        s = store.create_session()
        assert store.append_message(s.id, "user", None) is None
        assert store.append_message(s.id, None, "still works") is None
        assert store.message_count(s.id) == 0
