from server.ast_facts import extract

USER_BEFORE = """
from models import User

def get_user(id):
    return User.get(id)
"""

USER_AFTER = """
from functools import lru_cache
from models import User

@lru_cache(maxsize=1024)
def get_user(id):
    u = User.get(id)
    return {"id": u.id, "name": u.name, "email": u.email}
"""


def test_demo_fact_matches_contract():
    assert extract("services/user.py", USER_BEFORE, USER_AFTER) == [{
        "file": "services/user.py", "symbol": "get_user", "qualname": "get_user",
        "change_type": "return_changed",
        "before": "result of User.get()", "after": "dict with keys email, id, name",
    }]


def test_unchanged_file_gives_no_facts():
    assert extract("services/user.py", USER_BEFORE, USER_BEFORE) == []


def test_body_change_without_return_or_sig_change_gives_no_facts():
    after = USER_BEFORE.replace("return User.get(id)", "x = 1\n    return User.get(id)")
    assert extract("a.py", USER_BEFORE, after) == []


def test_signature_changed_with_return_annotation():
    before = "def f(a):\n    return a\n"
    after = "def f(a, b=2) -> int:\n    return a\n"
    assert extract("m.py", before, after) == [{
        "file": "m.py", "symbol": "f", "qualname": "f", "change_type": "signature_changed",
        "before": "a", "after": "a, b=2 -> int",
    }]


def test_signature_and_return_both_change():
    facts = extract("m.py", "def f(a):\n    return a\n", "def f(a, b):\n    return [a, b]\n")
    assert [f["change_type"] for f in facts] == ["signature_changed", "return_changed"]
    assert facts[1]["before"] == "a" and facts[1]["after"] == "list"


def test_added_and_removed():
    facts = extract("m.py", "def old(x):\n    pass\n", "def new(y, z):\n    pass\n")
    assert facts == [
        {"file": "m.py", "symbol": "new", "qualname": "new", "change_type": "added",
         "before": None, "after": "y, z"},
        {"file": "m.py", "symbol": "old", "qualname": "old", "change_type": "removed",
         "before": "x", "after": None},
    ]


def test_new_file_everything_added():
    facts = extract("new.py", "", "def f():\n    return 1\n")
    assert [(f["qualname"], f["change_type"]) for f in facts] == [("f", "added")]


def test_method_qualname_and_symbol():
    before = "class UserService:\n    def get_user(self, id):\n        return None\n"
    after = "class UserService:\n    def get_user(self, id):\n        return (id, 1)\n"
    [fact] = extract("s.py", before, after)
    assert (fact["qualname"], fact["symbol"]) == ("UserService.get_user", "get_user")
    assert (fact["before"], fact["after"]) == ("None", "tuple")


def test_multiple_returns_sorted_and_joined():
    before = "def f(x):\n    return x\n"
    after = "def f(x):\n    if x:\n        return None\n    return {'a': 1}\n"
    [fact] = extract("m.py", before, after)
    assert fact["after"] == "None or dict with keys a"


def test_nested_function_returns_ignored():
    before = "def f():\n    return 1\n"
    after = "def f():\n    def g():\n        return [1]\n    return 1\n"
    assert extract("m.py", before, after) == []


def test_bare_return_and_no_return_are_none():
    assert extract("m.py", "def f():\n    pass\n", "def f():\n    return\n") == []


def test_long_expression_truncated():
    after = "def f():\n    return " + " + ".join(["a"] * 40) + "\n"
    [fact] = extract("m.py", "def f():\n    return a\n", after)
    assert len(fact["after"]) == 60


def test_skip_non_python_and_syntax_errors():
    assert extract("README.md", "a", "b") == []
    assert extract("m.py", "def f(:", "def f():\n    pass\n") == []
