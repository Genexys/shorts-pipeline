"""Names a nested function of the pipeline reads must not be rebound later.

The pipeline's helpers are closures over run_generation_pipeline's locals, and
a closure reads a name's current value. On 2026-11-12 check() read `windows`,
the clips' usable footage, after the shot cap had rebound it to the beats'
timing: the web Short failed with "list indices must be integers or slices,
not str". This finds any helper that reads a name the function assigns again
after the helper is defined, apart from the ones rebound on purpose.
"""

import ast
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1] / "Backend" / "pipeline.py"

# Rebound deliberately, and read for their latest value: the research brief
# grows as sources are judged, and the footage is replaced by the checked set.
INTENDED = {"sources", "facts_block", "keywords", "footage", "beats", "openers", "script"}


def _outer_stores(node, found):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            found.setdefault(child.id, []).append(child.lineno)
        _outer_stores(child, found)
    return found


def test_no_helper_reads_a_name_rebound_after_it():
    tree = ast.parse(PIPELINE.read_text())
    outer = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_generation_pipeline"
    )
    stores = _outer_stores(outer, {})
    clashes = []
    for helper in ast.walk(outer):
        if not isinstance(helper, ast.FunctionDef) or helper is outer:
            continue
        local = {arg.arg for arg in helper.args.args} | {
            node.id for node in ast.walk(helper)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        reads = {
            node.id for node in ast.walk(helper)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        } - local
        for name in reads - INTENDED:
            lines = stores.get(name, [])
            if len(lines) > 1 and any(line > helper.lineno for line in lines):
                clashes.append(f"{helper.name} reads {name!r}, assigned at {lines}")
    assert clashes == []
