#!/usr/bin/env bash
# Воспроизведение находок проверки ветки proverka4.
# Запуск из корня репозитория tallystick:   bash bench/repro_proverka4.sh
# Проверено на proverka4 @ 48c8b8a. На других ветках цифры могут отличаться - это и есть смысл перезапуска.
# Репозиторий не меняется: всё пишется во временную папку (mktemp -d).
# Время работы ~1-2 минуты. Ненулевые коды выхода ниже - ожидаемые, это и есть результат.
set -u
cd "$(git rev-parse --show-toplevel)"
PY="$PWD/.venv/bin/python"; export PYTHONDONTWRITEBYTECODE=1
T() { "$PY" -m tallystick.cli "$@"; }
ex() { local label=$1; shift; "$@" >/dev/null 2>&1; echo "  $label -> exit $?"; }
D=$(mktemp -d); echo "branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD); temp: $D"

"$PY" - "$D" <<'EOF'
import json, sys
D = sys.argv[1]; E = "The capital of Australia is Sydney"
Q = {"role": "user", "content": "What is the capital of Australia?"}; F = {"role": "assistant", "content": E}
def c(n, a, i=None):
    x = {"type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
    if i: x["id"] = i
    return x
def r(content, n=None, i=None):
    x = {"role": "tool", "content": content}
    if n is not None: x["name"] = n
    if i: x["tool_call_id"] = i
    return x
def a(*calls): return {"role": "assistant", "content": None, "tool_calls": list(calls)}
def t(n, args, i, res): return [a(c(n, args, i)), r(res, n, i)]
save = t("save_note", {"key": "capital", "text": E}, "c1", "saved")
def notes(res): return [Q, *save, *t("read_note", {"key": "capital"}, "c2", res), F]
L = {
 "notes": notes(E),
 "notes_json": notes(json.dumps({"key": "capital", "text": E})),
 "notes_pretty": notes(json.dumps({"key": "capital", "text": E}, indent=2)),
 "notes_period": notes(E + "."),
 "notes_status": notes(E + "\n(1 note, 3 ms)"),
 "same_turn": [Q, a(c("save_note", {"key": "capital", "text": E}, "c1"), c("read_note", {"key": "capital"}, "c2")),
               r("saved", "save_note", "c1"), r(E, "read_note", "c2"), F],
 "anthropic_same_turn": [Q, {"role": "assistant", "content": [
        {"type": "tool_use", "id": "u1", "name": "save_note", "input": {"text": E}},
        {"type": "tool_use", "id": "u2", "name": "read_note", "input": {"key": "capital"}}]},
     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "u1", "content": "saved"},
                                  {"type": "tool_result", "tool_use_id": "u2", "content": E}]}, F],
 "same_name": [Q, *save, *t("read", {"key": "capital"}, "c2", E),
               *t("write_file", {"path": "db.conf", "content": "max_connections = 500"}, "c3", "ok"),
               *t("read", {"path": "db.conf"}, "c4", "max_connections = 500"), F],
 "positional": [Q, *save, a(c("get_weather", {"city": "Canberra"}), c("read_note", {"key": "capital"})),
                r(E), r("Sunny, 18 C"), F],
 "empty_name": [Q, *t("", {"text": E}, "c1", "saved"), *t("", {"key": "capital"}, "c2", E), F],
 "dash_name": [Q, *save, *t("-n", {"key": "capital"}, "c2", E), F],
 "own_json_envelope": [Q, *t("echo", {"text": E}, "c1", json.dumps({"echo": E})), F],
 "own_repr": [Q, *t("python", {"code": f'x = "{E}"\nx'}, "c1", repr(E)), F],
 "own_status_line": [Q, *t("python", {"code": f'print("{E}")'}, "c1", E + "\n[Execution time: 0.01s]"), F],
 "own_prefix": [Q, *t("save_note", {"text": E}, "c1", "Saved note: " + E), F],
 "own_noid_renamed": [Q, a(c("python", {"code": f'print("{E}")'})), r(E, "functions.python"), F],
 "own_id_rewritten": [Q, a(c("python", {"code": f'print("{E}")'}, "call_1")), r(E, "python", "toolu_1"), F],
 "own_control": [Q, a(c("python", {"code": f'print("{E}")'})), r(E, "python"), F],
}
for k, v in L.items():
    json.dump(v, open(f"{D}/{k}.json", "w"))
json.dump([{"claims": [E]}, {"credits": [{"artifact_id": "t4", "quote": E}]}], open(f"{D}/answers_t4.json", "w"))
EOF

echo; echo "== 1. Обходы гейта"
ex "check-trace notes.json (контроль)" T check-trace "$D/notes.json"
ex "propose notes.json (fake)" T propose "$D/notes.json" -o "$D/posted.json" --proposer fake --script "$D/answers_t4.json"
ex "audit posted.json" T audit "$D/posted.json"
"$PY" -c "
import json, sys, tallystick as t
p = sys.argv[1]; raw = json.load(open(p))
print('  1a. warnings in posted _meta:', len(raw['_meta']['echo_warning_details']))
print('      tallystick.audit(posted).books_balance =', t.audit(p).books_balance)
print('      tallystick.check_trace(run, meta).verdict =', repr(t.check_trace(t.load_run(raw), meta=raw['_meta']).verdict))" "$D/posted.json"
mkdir "$D/v075" && git archive 93ab30d | tar -x -C "$D/v075"
(cd "$D/v075" && "$PY" -m tallystick.cli convert "$D/notes.json" -o "$D/conv_v075.json" >/dev/null)
ex "1b. check-trace trace converted by 93ab30d (v0.7.5)" T check-trace "$D/conv_v075.json"
T convert "$D/notes.json" -o "$D/conv_now.json" >/dev/null
ex "1c. check-trace conv_now.json --tool-returns-model-text read_note" T check-trace "$D/conv_now.json" --tool-returns-model-text read_note
ex "    check-trace notes.json    --tool-returns-model-text read_note" T check-trace "$D/notes.json" --tool-returns-model-text read_note
for k in notes_json notes_pretty notes_period notes_status same_turn anthropic_same_turn; do
  ex "1d. check-trace $k" T check-trace "$D/$k.json"
done
ex "1d. propose notes_json (fake)" T propose "$D/notes_json.json" -o "$D/posted_json.json" --proposer fake --script "$D/answers_t4.json"
ex "1d. audit posted_json" T audit "$D/posted_json.json"
printf "  1d. BOOKS BALANCE printed: "; T audit "$D/posted_json.json" | grep -c "BOOKS BALANCE"

echo; echo "== 2. Подтверждение по имени"
ex "2a. same_name" T check-trace "$D/same_name.json"
ex "2a. same_name --accept-echo-warning read" T check-trace "$D/same_name.json" --accept-echo-warning read
echo "  2b. positional --quiet (stderr):"; T check-trace "$D/positional.json" --quiet 2>&1 | sed 's/^/     | /'
ex "2b. positional --accept-echo-warning get_weather" T check-trace "$D/positional.json" --accept-echo-warning get_weather
ex "2c. empty_name --accept-echo-warning tool" T check-trace "$D/empty_name.json" --accept-echo-warning tool
T check-trace "$D/dash_name.json" | grep "to confirm" | sed 's/^/  2d. printed: /'
ex "2d. dash_name --accept-echo-warning -n (as printed)" T check-trace "$D/dash_name.json" --accept-echo-warning -n
ex "2d. dash_name --accept-echo-warning=-n" T check-trace "$D/dash_name.json" --accept-echo-warning=-n

echo; echo "== 3. Правило отвечающего вызова: вид артефакта (tool_result = доказательство)"
for k in own_json_envelope own_repr own_status_line own_prefix own_noid_renamed own_id_rewritten own_control; do
  printf "  %-20s" "$k"; T convert "$D/$k.json" -o "$D/$k.out.json" | sed -n 2p
done

echo; echo "== 4 и 7. Корпус AgentHallu: гейт и числа читателя"
"$PY" - <<'EOF'
import io, json, sys, tempfile, contextlib
from pathlib import Path
sys.path[:0] = [".", "bench"]
from tallystick.cli import main
from tallystick.adapters import agenthallu, openai_chat as oc
import openai_roundtrip as rt
d = Path(tempfile.mkdtemp()); log, rep = d / "l.json", d / "r.json"
modes = {"no flags": [], "4 echo tools declared": [x for n in sorted(agenthallu.ECHO_TOOLS) for x in ("--tool-returns-model-text", n)]}
for label, fl in modes.items():
    n = ex0 = gate = only = ee = eb = tools = et = ed = stricter = laxer = 0
    for p in sorted(Path("bench/work-agenthallu/AgentHallu").rglob("*.json")):
        try: o = json.loads(p.read_text())
        except ValueError: continue
        if not isinstance(o, dict) or "history" not in o: continue
        msgs = rt.render(o); n += 1
        tr = oc.to_trace(msgs, model_text_tools=agenthallu.ECHO_TOOLS if fl else None)
        ee += len(tr["_meta"]["echoes_from_earlier_turns"]); eb += len(tr["_meta"]["echoed_back_tool_results"])
        if not fl:
            kinds = {a["artifact_id"]: a["kind"] for a in tr["artifacts"]}
            tools += sum(k.startswith("t") for k in kinds)
            for k, m in enumerate(msgs):
                if m["role"] == "tool" and m.get("name") in agenthallu.ECHO_TOOLS:
                    et += 1; ed += kinds.get(f"t{k}") in ("intermediate", "final_answer")
        else:
            for (w, _), (h, _) in zip(rt.shape(agenthallu.to_trace(o)), rt.shape(tr)):
                stricter += w == "root" and h == "derived"; laxer += w == "derived" and h == "root"
        log.write_text(json.dumps(msgs)); rep.unlink(missing_ok=True)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(["check-trace", str(log), "--quiet", "--json", str(rep), *fl])
        reasons = json.loads(rep.read_text())["gate"]["reasons"]
        ex0 += code == 0; gate += "unreviewed_echo_warnings" in reasons; only += reasons == ["unreviewed_echo_warnings"]
    print(f"  [{label}] {n} trajectories; exit 0: {ex0}; exit 1 with unreviewed_echo_warnings: {gate} ({gate/n:.1%});"
          f" exit 1 ONLY for it: {only} ({only/(ex0+only):.1%} of those that would pass)")
    print(f"      echoes_from_earlier_turns: {ee}; echoed_back_tool_results in _meta: {eb}")
    if not fl: print(f"      tool artifacts: {tools}; echo-tool results: {et}, demoted by the rule alone: {ed}")
    else: print(f"      vs native reader: stricter {stricter}, laxer {laxer}")
EOF

echo; echo "== 5. Время чтения (to_trace), один ход из N параллельных вызовов по 21 КБ; и N мелких ходов"
"$PY" - <<'EOF'
import io, json, time, tempfile, contextlib
from pathlib import Path
from tallystick.adapters.openai_chat import to_trace
from tallystick.cli import main
CODE = 'x = compute("value")\n' * 1000
def wide(n, mode):
    calls = [{"type": "function", "function": {"name": "python", "arguments": json.dumps({"code": CODE})},
              **({"id": f"c{k}"} if mode == "ids" else {})} for k in range(n)]
    m = [{"role": "user", "content": "q"}, {"role": "assistant", "content": None, "tool_calls": calls}]
    extra = lambda k: {"tool_call_id": f"c{k}", "name": "python"} if mode == "ids" else ({"name": "python"} if mode == "name" else {})
    m += [{"role": "tool", "content": f"result {k}", **extra(k)} for k in range(n)]
    m.append({"role": "assistant", "content": "done"})
    s = time.perf_counter(); to_trace(m); return time.perf_counter() - s
for mode in ("ids", "name", "none"):
    print(f"  results with {mode:<5}", "  ".join(f"N={n}: {wide(n, mode):.2f}s" for n in (25, 50, 100)))
def small(n):
    m = [{"role": "user", "content": "q"}]
    for k in range(n):
        m += [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{k}", "type": "function", "function": {"name": "f", "arguments": json.dumps({"k": k})}}]},
              {"role": "tool", "tool_call_id": f"c{k}", "name": "f", "content": f"r{k}"}]
    m.append({"role": "assistant", "content": "done"})
    p = Path(tempfile.mkdtemp()) / "l.json"; p.write_text(json.dumps(m))
    s = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        main(["check-trace", str(p), "--quiet"])
    return time.perf_counter() - s, sum(len(st["inputs"]) for st in to_trace(m)["steps"])
for n in (2000, 4000, 8000):
    secs, refs = small(n)
    print(f"  check-trace, {n} small turns: {secs:.2f}s, step input references: {refs:,}")
def turns(n):
    m = [{"role": "user", "content": "q"}]
    for k in range(n):
        m += [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{k}", "type": "function", "function": {"name": "python", "arguments": json.dumps({"code": CODE})}}]},
              {"role": "tool", "tool_call_id": f"c{k}", "name": "python", "content": f"result {k}"}]
    m.append({"role": "assistant", "content": "done"})
    s = time.perf_counter(); to_trace(m); return time.perf_counter() - s
print("  commit 0deb9e8 figure, 21 KB code per call:", "  ".join(f"{n} turns {turns(n):.2f}s" for n in (200, 400, 800)))
EOF

echo; echo "== 6. Тесты: проверка группы B и test_note_read_back переживает удаление самого предупреждения"
for mode in M1 M5; do
  mkdir "$D/$mode" && git archive HEAD | tar -x -C "$D/$mode" && ln -s "$PWD/bench/work-agenthallu" "$D/$mode/bench/work-agenthallu"
  "$PY" - "$D/$mode/tallystick/adapters/openai_chat.py" "$mode" <<'EOF'
import sys
p, mode = sys.argv[1:]; s = open(p).read()
s = s.replace("if line and line in earlier_pieces:", "if False:")          # no echo warning is ever produced
if mode == "M5":                                                            # ...and every result is listed as a guessed name
    s = s.replace('            aid = f"t{k}"\n', '            aid = f"t{k}"\n            guessed.append(f"tool[{k}] -> x")\n')
open(p, "w").write(s)
EOF
  printf "  %s: " "$mode"
  (cd "$D/$mode" && "$PY" -m pytest -q -p no:cacheprovider tests/test_openai_chat_laundering.py::test_note_read_back_in_a_later_turn \
     tests/test_openai_chat_turn_width.py -k "earlier_turn or note_read_back" 2>&1 | tail -1)
done

echo; echo "== 7. Числа из коммитов: тесты и файл результатов"
"$PY" bench/openai_roundtrip.py bench/work-agenthallu/AgentHallu --out "$D/rt.txt" >/dev/null \
  && cmp -s "$D/rt.txt" bench/results/openai-roundtrip.txt && echo "  bench/results/openai-roundtrip.txt: identical to a fresh run"
for c in 0deb9e8 bee91ae e199d64 48c8b8a; do
  mkdir "$D/$c" && git archive $c | tar -x -C "$D/$c" && ln -s "$PWD/bench/work-agenthallu" "$D/$c/bench/work-agenthallu"
  printf "  full suite at %s: " $c; (cd "$D/$c" && "$PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -1)
done
cp "$D/bee91ae/tests/test_check_trace_echo_gate.py" "$D/0deb9e8/tests/"
printf "  bee91ae gate tests on 0deb9e8: "; (cd "$D/0deb9e8" && "$PY" -m pytest -q -p no:cacheprovider tests/test_check_trace_echo_gate.py 2>&1 | tail -1)
cp "$D/e199d64/tests/test_check_trace_echo_gate.py" "$D/bee91ae/tests/"
printf "  e199d64 gate tests on bee91ae: "; (cd "$D/bee91ae" && "$PY" -m pytest -q -p no:cacheprovider tests/test_check_trace_echo_gate.py 2>&1 | tail -1)
cp "$D/48c8b8a/tests/test_audit_echo_gate.py" "$D/e199d64/tests/"
printf "  48c8b8a audit tests on e199d64: "; (cd "$D/e199d64" && "$PY" -m pytest -q -p no:cacheprovider tests/test_audit_echo_gate.py 2>&1 | tail -1)
echo; echo "repo status after run (empty = untouched):"; git status --short
