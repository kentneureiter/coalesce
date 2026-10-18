"""judge.py tests with a fake Anthropic client. No real API calls."""
import json
from types import SimpleNamespace

import pytest

from server import judge

CHANGE = {"from_user": "Poshitha", "branch": "feature/caching", "task": "add caching to user lookups",
          "last_message": "get_user now returns a dict.",
          "facts": [{"file": "services/user.py", "symbol": "get_user", "qualname": "get_user",
                     "change_type": "return_changed", "before": "result of User.get()",
                     "after": "dict with keys email, id, name"}]}
OTHER = {"user_name": "Kent", "branch": "feature/orders", "prompt": "email users their order history",
         "files_changed": ["orders.py"]}


class FakeClient:
    def __init__(self, text=None, stop_reason="end_turn", error=None):
        self.text, self.stop_reason, self.error, self.calls = text, stop_reason, error, []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=self.text)])


@pytest.fixture
def fake(monkeypatch):
    def use(**kw):
        client = FakeClient(**kw)
        monkeypatch.setattr(judge, "_client", client)
        return client
    return use


def test_prompt_contains_both_sides():
    p = judge.build_prompt(CHANGE, OTHER)
    assert "session Poshitha, branch feature/caching" in p
    assert "Task: add caching to user lookups" in p
    assert "- get_user in services/user.py changed its return value." in p
    assert "session Kent, branch feature/orders" in p
    assert "Task: email users their order history" in p
    assert "Files it is working on: orders.py" in p
    assert "no instructions" in p


def test_relevant_returns_note_and_uses_right_settings(fake):
    client = fake(text=json.dumps({"relevant": True, "note": "get_user now returns a dict; orders.py reads user.email."}))
    assert judge.judge(CHANGE, OTHER) == "get_user now returns a dict; orders.py reads user.email."
    call = client.calls[0]
    assert call["model"] == judge.MODEL and call["max_tokens"] == 200
    assert call["thinking"] == {"type": "disabled"}
    assert call["output_config"]["format"]["schema"] == judge.SCHEMA


def test_not_relevant_returns_none(fake):
    fake(text=json.dumps({"relevant": False, "note": "unrelated"}))
    assert judge.judge(CHANGE, OTHER) is None


@pytest.mark.parametrize("kw", [
    {"error": TimeoutError()},
    {"text": "not json"},
    {"text": json.dumps({"relevant": True, "note": ""})},
    {"text": None, "stop_reason": "refusal"},
    {"text": "{\"relevant\": tr", "stop_reason": "max_tokens"},
])
def test_any_problem_means_not_relevant(fake, kw):
    assert judge.judge(CHANGE, OTHER) is None


def test_no_api_key_means_not_relevant(monkeypatch):
    monkeypatch.setattr(judge, "_client", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert judge.judge(CHANGE, OTHER) is None
