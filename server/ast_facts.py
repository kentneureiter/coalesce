"""STUB until step 6. Real version: contract 10.1 (return/signature diffing).

For now: report every top-level function or method whose source changed, was
added, or was removed, so the code-link path works end to end.
"""
import ast


def _functions(source: str) -> dict[str, tuple[str, str]]:
    """qualname -> (symbol, source text) for top-level functions and class methods."""
    tree = ast.parse(source)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = (node.name, ast.unparse(node))
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{item.name}"] = (item.name, ast.unparse(item))
    return out


def extract(path: str, before: str, after: str) -> list[dict]:
    if not path.endswith(".py"):
        return []
    try:
        old, new = _functions(before or ""), _functions(after or "")
    except SyntaxError:
        return []
    facts = []
    for qual in sorted(old.keys() | new.keys()):
        if qual in old and qual in new and old[qual][1] == new[qual][1]:
            continue
        change = "added" if qual not in old else "removed" if qual not in new else "modified"
        symbol = (new.get(qual) or old.get(qual))[0]
        facts.append({"file": path, "symbol": symbol, "qualname": qual,
                      "change_type": change, "before": None, "after": None})
    return facts
