# Pre-Codex baseline

Prepared from the private-configured v1.2.1 build with credentials and generated runtime artifacts removed.

## Baseline validation

- `python -m compileall -q app tests .` — passes.
- `python -m pytest -q` — **45 passed**.
- `node --check app/static/app.js` — passes when Node is available.

## Known baseline issue to investigate

Running `pytest -q` directly in this environment fails test collection with `ModuleNotFoundError: No module named 'app'`, while `python -m pytest -q` passes all 45 tests. Codex should make test invocation deterministic rather than depending on the launcher/module invocation path.

## Sanitization

- `.env` removed.
- private configuration note removed.
- Python/pytest caches removed.
- generated SQLite runtime databases removed.
- `.env.example` retains placeholders only.
- scan for populated assignments to known credential variables returned clean.
