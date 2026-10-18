from datetime import datetime, timedelta, timezone

from server import relevance as r

NOW = datetime(2026, 10, 18, 4, 0, tzinfo=timezone.utc)
FACT = {"file": "services/user.py", "symbol": "get_user", "qualname": "get_user",
        "change_type": "return_changed", "before": "result of User.get()",
        "after": "dict with keys email, id, name"}

CHECKOUT = "\n".join([
    "from services.user import get_user",
    "",
    "def checkout(user_id, cart):",
    "    user = get_user (user_id)",
    "    return user.email",
])


def test_find_use_line_prefers_call_over_import():
    assert r.find_use_line("get_user", CHECKOUT) == 4


def test_find_use_line_falls_back_to_import():
    assert r.find_use_line("get_user", "from services.user import get_user\nx = 1") == 1


def test_find_use_line_call_with_space():
    assert r.find_use_line("get_user", "x = 1\ny = get_user  (3)") == 2


def test_find_use_line_ignores_definition_and_longer_names():
    content = "def get_user(id):\n    pass\nget_user_by_email(x)\nmy_get_user(1)"
    assert r.find_use_line("get_user", content) is None


def test_find_use_line_none_for_empty():
    assert r.find_use_line("get_user", "") is None


def test_fact_key():
    assert r.fact_key(FACT) == "services/user.py|get_user|return_changed|dict with keys email, id, name"


def test_code_link_note_matches_contract_example():
    note = r.code_link_note(FACT, "Poshitha", "claude", "feature/caching",
                            NOW - timedelta(minutes=4), "checkout.py", 42, now=NOW)
    assert note == (
        "Teammate update (Poshitha, claude session on feature/caching, 4 min ago, not merged yet): "
        "get_user in services/user.py changed its return value. Before: result of User.get(). "
        "Now: dict with keys email, id, name. Your file checkout.py calls get_user on line 42."
    )


def test_notes_are_facts_not_instructions_and_short():
    long = {**FACT, "after": "x" * 2000}
    for note in [
        r.code_link_note(long, "P", "claude", "b", NOW, "f.py", 1, now=NOW),
        r.judge_note("y" * 2000, "K", "gemini", "b", NOW, now=NOW),
        r.human_note("P", "z" * 2000, "slack"),
    ]:
        assert len(note) <= r.MAX_NOTE
        low = note.lower()
        assert "you must" not in low and "instruction" not in low


def test_judge_note():
    note = r.judge_note("orders.py reads user.email.", "Kent", "gemini", "feature/orders",
                        NOW - timedelta(minutes=2), now=NOW)
    assert note == ("Teammate update (Kent, gemini session on feature/orders, 2 min ago, "
                    "not merged yet): orders.py reads user.email.")


def test_human_note():
    assert (r.human_note("Poshitha", "we're switching to dicts for user data", "slack")
            == "Team note from Poshitha (via Slack): we're switching to dicts for user data.")


def test_board_text_excludes_self_and_labels_in_progress():
    entries = [
        {"session_id": "s1", "user_name": "Poshitha", "agent": "claude", "branch": "feature/caching",
         "prompt": "add caching", "status": "done", "files_changed": ["services/user.py"]},
        {"session_id": "s3", "user_name": "Kent", "agent": "gemini", "branch": "feature/checkout",
         "prompt": "build the checkout flow", "status": "in_progress", "files_changed": []},
    ]
    assert r.board_text(entries, exclude_session="s3") == (
        "Coalesce team board (other active agent sessions):\n"
        '- Poshitha, claude, feature/caching: "add caching". Files: services/user.py'
    )
    assert "In progress (not yet changed)." in r.board_text(entries, exclude_session="s1")
    assert r.board_text(entries[:1], exclude_session="s1") == ""


def test_ago():
    assert r.ago(NOW, now=NOW) == "just now"
    assert r.ago(NOW - timedelta(minutes=90), now=NOW) == "1 h ago"
