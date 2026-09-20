# Contributing

The most useful thing you can send is a case where the audit is wrong: a trace
(or a log `check-trace` reads) where it flags something honest, or lets
something through that it should have named. Open an issue with the file
attached, or the smallest version of it that still shows the behaviour.

Before a pull request:

```bash
pip install -e ".[dev,langchain]"
pytest -q
```

Two rules the build enforces, so they are worth knowing first:

- Nothing on the verdict path may import a model SDK, an HTTP client, `random`,
  `time` or `uuid`. `tests/test_no_model_imports.py` walks the AST and fails the
  build. The model side lives in `tallystick/propose/` and nowhere else.
- A number in the README or under `docs/` either names the run it came from or
  comes with the command that recomputes it. Several tests exist only to hold
  that line.

Documentation changes are welcome on the same terms as code: `README.md`,
`docs/`, `bench/README.md` and `bench/HISTORY.md` are all read by tests.

---
Built by Katia Engalycheva, co-authored with Claude (Anthropic) | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
