"""Facts about what changed in a Python file, from its before/after source (contract 10.1).

extract("services/user.py", before, after) ->
  [{"file": "services/user.py", "symbol": "get_user", "qualname": "get_user",
    "change_type": "return_changed",
    "before": "result of User.get()", "after": "dict with keys email, id, name"}]
"""
import ast

MAX_EXPR = 60


def _return_shape(value: ast.expr | None) -> str:
    if value is None or (isinstance(value, ast.Constant) and value.value is None):
        return "None"
    if isinstance(value, ast.Dict) and value.keys and all(
            isinstance(k, ast.Constant) for k in value.keys):
        return "dict with keys " + ", ".join(sorted(str(k.value) for k in value.keys))
    if isinstance(value, ast.Call):
        return f"result of {ast.unparse(value.func)}()"
    if isinstance(value, ast.List):
        return "list"
    if isinstance(value, ast.Tuple):
        return "tuple"
    return ast.unparse(value)[:MAX_EXPR]


def _returns(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Sorted return shapes of func's own returns (not nested functions), joined with ' or '."""
    shapes = set()
    stack = list(func.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Return):
            shapes.add(_return_shape(node.value))
        stack.extend(ast.iter_child_nodes(node))
    return " or ".join(sorted(shapes or {"None"}))


def _sig(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    sig = ast.unparse(func.args)
    if func.returns is not None:
        sig += f" -> {ast.unparse(func.returns)}"
    return sig


def _functions(source: str) -> dict[str, dict]:
    """qualname -> {symbol, sig, returns} for top-level functions and methods of top-level classes."""
    out = {}
    funcs = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.parse(source).body:
        if isinstance(node, funcs):
            out[node.name] = {"symbol": node.name, "sig": _sig(node), "returns": _returns(node)}
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, funcs):
                    out[f"{node.name}.{item.name}"] = {
                        "symbol": item.name, "sig": _sig(item), "returns": _returns(item)}
    return out


def extract(path: str, before: str, after: str) -> list[dict]:
    """Facts for one file. Non-.py files, or files where either side fails to parse, give []."""
    if not path.endswith(".py"):
        return []
    try:
        old, new = _functions(before or ""), _functions(after or "")
    except SyntaxError:
        return []

    facts = []

    def fact(qual, symbol, change_type, b, a):
        facts.append({"file": path, "symbol": symbol, "qualname": qual,
                      "change_type": change_type, "before": b, "after": a})

    for qual in sorted(old.keys() | new.keys()):
        o, n = old.get(qual), new.get(qual)
        if o is None:
            fact(qual, n["symbol"], "added", None, n["sig"])
        elif n is None:
            fact(qual, o["symbol"], "removed", o["sig"], None)
        else:
            if o["sig"] != n["sig"]:
                fact(qual, n["symbol"], "signature_changed", o["sig"], n["sig"])
            if o["returns"] != n["returns"]:
                fact(qual, n["symbol"], "return_changed", o["returns"], n["returns"])
    return facts
