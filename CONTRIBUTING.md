# Contributing

Contributions are welcome. This is a small open-source project with a
single maintainer; please keep that in mind when opening issues or pull
requests — response times are not always immediate, and substantial new
features may need to wait on a roadmap decision.

## Reporting bugs

Open an issue at
<https://github.com/ykshah1309/neuro-research-discovery-mcp/issues> using
the **Bug report** template. The most helpful reports include:

- the exact tool name and the input you called it with,
- the structured `ToolError` returned (if any), or the audit-log JSON line,
- your Python version (`python --version`),
- whether you're hitting upstream APIs directly or working from cached data,
- a minimal reproduction script if you have one.

## Proposing features or behavior changes

Open an issue using the **Feature request** template before writing code.
This is especially important for changes that touch the MCP tool surface
(adding or renaming tools, changing input or output schemas) because those
are part of the public contract checked by `tests/test_doc_drift.py`.

For new sources (Crossref, OpenAlex, Semantic Scholar, etc.), see the
explicit v0.4 acceptance criteria in [`ROADMAP_v0.4.md`](ROADMAP_v0.4.md)
before opening a PR.

## Development setup

```bash
git clone https://github.com/ykshah1309/neuro-research-discovery-mcp
cd neuro-research-discovery-mcp
pip install -e ".[dev,web]" -c constraints-dev.txt
pytest tests/ --ignore=tests/test_integration.py
```

The unit suite (112 tests, ~15 s) runs offline against mocked upstream
APIs. The opt-in integration suite (`pytest -m integration`) hits the
live OpenNeuro, NeuroVault, and PubMed APIs and requires
`PUBMED_EMAIL` set to a real address.

## Pull request expectations

- Add or update tests for the change. Unit tests stay under the offline
  mocked-HTTP pattern in `tests/conftest.py`.
- If you touch the README's headline tool count, security-notes field
  list, or audit-log field list, update them in the same commit;
  `tests/test_doc_drift.py` enforces the README↔code invariants.
- If you add or rename a tool, update the README's "Available tools"
  table and the relevant section of [`docs/EXAMPLES.md`](docs/EXAMPLES.md).
- Run the full suite locally before opening the PR. CI will re-run on
  every push.

## Code style

The project uses standard formatting (PEP 8). No project-wide formatter
is enforced; match the style of the surrounding code. Async-first
throughout; the only sync call paths are the biopython Entrez wrappers
in `clients/pubmed.py`, which are intentionally bounded to
`asyncio.to_thread`.

## License

By contributing you agree that your contributions will be licensed
under the MIT License (see [`LICENSE`](LICENSE)).

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).
Be respectful in issues, PRs, and any other project space.
