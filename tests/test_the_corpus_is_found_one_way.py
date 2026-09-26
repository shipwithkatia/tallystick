"""Every test that needs the AgentHallu corpus looks for it the same way.

The corpus is too large for git, so the project's way of pointing at one is
`TALLYSTICK_AGENTHALLU`, with `bench/work-agenthallu/AgentHallu` as the
fallback
inside the tree. Two test modules use the corpus and one of them, until this
test existed, read only the fallback: setting the variable woke eight tests and
left the ninth skipping, with a reason that reads exactly like the other eight.
A skip is a silent state - nothing fails, nothing is printed in the default
run - so the odd one out is invisible unless something asks.

This is rule 13 of the project's rules in a new shape: the rule says a test
needing data from outside the repository guards nothing for a stranger. Here
the means to give it that data exists and does not reach everywhere.

What this does not check, named rather than left to be found:

- It reads source, not behaviour. A module that asks the variable and then
  ignores the answer passes here; only a run with the variable pointed
  somewhere else catches that, and the fix this guards was proved that way.
- It finds the modules by the fallback directory written as a quoted word of
  its own, in double quotes: the same path in single quotes is not found at
  all, and `bench/mutations/mutate.py` writes it where this does not look.
  Two scripts under `bench/` write it inside a longer path, as a
  default for a command-line option (`"bench/work-agenthallu/AgentHallu/..."`),
  and are not in the list at all; two more name the working directory beside
  the corpus, or only in their usage text. They are tools run by hand with that
  option, not tests a stranger runs, so the variable is not the way they are
  pointed at a corpus - but the boundary is here rather than left to be found.
  `test_the_search_itself_finds_something` below is what keeps the list from
  quietly becoming empty.
- The variable must appear in code rather than in a comment or a docstring,
  which is checked by reading the syntax tree. A first version of this test
  accepted a mention anywhere in the file, so putting the old line back with
  the variable named in a comment above it passed - found by an outside review
  doing exactly that.
- Where it looks: `tests/test_*.py` and `bench/*.py`, the top level of each.
  `bench/mutations/mutate.py` writes the fallback path and never reads the
  variable, and is not looked at. That is deliberate - it is a tool for one
  measurement, not a test a stranger runs - but the boundary belongs here
  rather than in someone's later surprise.
- A note that calls something - `str("NAME")`, a lambda nobody runs - counts as
  code, because the rule below asks whether the statement does anything, not
  whether anyone wanted it to. A fifth review found that shape and judged it
  already inside this boundary; it is written out here so the next reader does
  not have to rediscover it.
- What counts as asking: any string equal to the name, anywhere in the code of
  the module. An unused `_UNUSED = "TALLYSTICK_AGENTHALLU"` satisfies it. The
  first item above is about a module that asks and ignores the answer; this one
  is about a module that never asks and looks as if it did.
- The reverse mistake: a module that builds the name out of pieces is named as
  an offender though it is honest. That is the price of asking for the name as
  one string, and is left standing - the alternative is a reading loose enough
  to accept the shapes above.
- `test_the_search_itself_finds_something` asks for two modules and two is what
  there are, so if a third appears, one of the three dropping out of the search
  passes unnoticed. Raising the number now would only mean writing today's
  count twice.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The name every corpus lookup must go through.
VARIABLE = "TALLYSTICK_AGENTHALLU"

#: The fallback path, written as it appears in source.
FALLBACK = re.compile(r'"work-agenthallu"')


def _strings_in_code(text: str) -> set[str]:
    """Every string the module evaluates for its own sake. Comments are not in
    the syntax tree at all. A string standing alone as a statement - a
    docstring, or a note left between two lines of code - is prose, and is
    dropped by which node it is, not by what it says: a first version dropped
    it by value, and a module whose docstring read exactly the name would have
    lost its real use of the name along with it.
    """
    tree = ast.parse(text)
    prose = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and _a_statement_that_does_nothing(node):
            prose.update(id(inner) for inner in ast.walk(node)
                         if isinstance(inner, ast.Constant)
                         and isinstance(inner.value, str))
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in prose}


#: A statement that stands alone and does something: these are what separate
#: code from a note. Everything else evaluated for its own sake and thrown away
#: is prose, whatever it is written as.
DOES_SOMETHING = (ast.Call, ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr)


def _a_statement_that_does_nothing(node) -> bool:
    """Three rounds of review walked past three different spellings of a note:
    a bare string, a string with a trailing comma (a tuple), a one-entry
    dictionary. Each time the fix listed one more shape, and the next round
    found the shape after it - the project's rule for exactly this is that two
    fixes pulling the same way mean the wrong knob, so this asks what a note
    is rather than what it looks like. A note is a statement whose value is
    computed and dropped, and nothing in it does anything.

    The price, measured rather than guessed: a lone `os.environ["NAME"]`,
    written as a statement and its value discarded, is read as a note too. It
    is a statement that does nothing - it cannot be how a module reads the
    variable - and no module in this repository has one.
    """
    return not any(isinstance(inner, DOES_SOMETHING)
                   for inner in ast.walk(node))


def _modules_that_name_the_fallback():
    for path in sorted((ROOT / "tests").glob("test_*.py")) + \
                sorted((ROOT / "bench").glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if FALLBACK.search(text):
            yield path, text


def _modules_that_do_not_ask():
    """The selection itself, in one place. It used to sit inside the test
    below, and the self-check exercised `_strings_in_code` instead: a seventh
    review put the first version of the reading back - the whole file rather
    than its code - planted the original defect, and every test stayed green.
    A test of a helper says nothing about the line that calls it."""
    return [str(p.relative_to(ROOT)) for p, text in
            _modules_that_name_the_fallback()
            if VARIABLE not in _strings_in_code(text)]


def test_every_corpus_lookup_reads_the_variable():
    offenders = _modules_that_do_not_ask()
    assert not offenders, (
        f"these name the fallback corpus path but never ask {VARIABLE} in "
        f"code, so pointing the variable at a corpus elsewhere leaves them "
        f"skipping while their neighbours run: {offenders}")


def test_the_search_itself_finds_something():
    """The search above must be able to fail: if no module names the fallback,
    the test above passes by finding nothing, which guards nothing."""
    found = [str(p.relative_to(ROOT))
             for p, _ in _modules_that_name_the_fallback()]
    assert len(found) >= 2, found


def test_the_selection_runs_on_a_tree_that_hides_the_defect(tmp_path,
                                                           monkeypatch):
    """The self-check with a referent outside this file: a scratch tree
    carrying the very defect this guard exists for - the old line, with the
    variable named only in a comment above it. The selection must name that
    module. Proved through `_modules_that_do_not_ask`, the function the test
    above calls, not through the helper underneath it."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "bench").mkdir()
    (tmp_path / "tests" / "test_guilty.py").write_text(
        f"# {VARIABLE} is documented elsewhere\n"
        f'CORPUS = ROOT / "bench" / "work-agenthallu" / "AgentHallu"\n',
        encoding="utf-8")
    # A second guilty module, and the reason it is here: a comment does not
    # exist in the syntax tree at all, so the module above proves only that
    # the selection reads code rather than the whole file. This one names the
    # variable in a note - a string standing alone - which *is* in the tree,
    # and so it is caught only if the selection drops prose. An outside review
    # walked past the first version by switching the call to `_strings_in_code`
    # off; with this module that switch goes red.
    # The note goes *after* the code on purpose: a string on a module's first
    # line is its docstring, and dropping only docstrings is a narrower rule
    # than dropping prose. With the note first, that narrower rule passes this
    # tree unnoticed - a ninth review found exactly that.
    (tmp_path / "tests" / "test_guilty_note.py").write_text(
        f'CORPUS = ROOT / "bench" / "work-agenthallu" / "AgentHallu"\n'
        f'"{VARIABLE}"\n',
        encoding="utf-8")
    (tmp_path / "tests" / "test_honest.py").write_text(
        f'CORPUS = os.environ.get("{VARIABLE}",\n'
        f'                        ROOT / "work-agenthallu")\n',
        encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)

    assert _modules_that_do_not_ask() == ["tests/test_guilty.py",
                                          "tests/test_guilty_note.py"], \
        _modules_that_do_not_ask()


def test_a_mention_in_prose_does_not_count():
    """The hole an outside review found: the first version of this test read
    the whole file, so the old line plus the variable named in a comment
    passed. This proves the reading used above refuses prose and keeps code.

    Every piece of prose here says the name and nothing else. A later review
    pointed out why that matters: the test above asks whether the name is one
    of the strings, so prose that merely contains the name - `Point NAME at
    it.` - is refused by any reading at all, and a case like that proves
    nothing about the reading being used. Only prose equal to the name can
    tell the two apart.
    """
    in_a_comment = f"# {VARIABLE}\nCORPUS = 1\n"
    in_a_docstring = f'"""{VARIABLE}"""\nCORPUS = 1\n'
    in_a_note = f'CORPUS = 1\n"{VARIABLE}"\n'
    # A note with a trailing comma is a tuple, not a bare string: an outside
    # review walked an earlier version past this shape.
    in_a_note_with_a_comma = f'CORPUS = 1\n"{VARIABLE}",\n'
    # A one-entry dictionary: the shape a fourth review walked past, and the
    # reason this asks what a note does rather than what it is written as.
    in_a_note_as_a_dict = f'CORPUS = 1\n{{"{VARIABLE}": 1}}\n'
    # Standing alone and still code: a call is not a note.
    in_a_call = f'print("{VARIABLE}")\n'
    in_code = f'CORPUS = os.environ.get("{VARIABLE}", 1)\n'
    # Both at once: the module says the name in prose and asks for it in code.
    # Dropping prose by value rather than by node loses the second with the
    # first, and this module would be named as an offender for a lookup it
    # does have.
    in_both = f'"""{VARIABLE}"""\nCORPUS = os.environ.get("{VARIABLE}", 1)\n'
    assert VARIABLE not in _strings_in_code(in_a_comment)
    assert VARIABLE not in _strings_in_code(in_a_docstring)
    assert VARIABLE not in _strings_in_code(in_a_note)
    assert VARIABLE not in _strings_in_code(in_a_note_with_a_comma)
    assert VARIABLE not in _strings_in_code(in_a_note_as_a_dict)
    assert VARIABLE in _strings_in_code(in_a_call)
    assert VARIABLE in _strings_in_code(in_code)
    assert VARIABLE in _strings_in_code(in_both)
