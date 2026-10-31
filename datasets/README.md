# ProvingGround datasets

Generated evaluation suites (JSONL). Do not edit by hand — regenerate with:

    uv run python scripts/generate_datasets.py

Each line is one `TestCase`. Reference answers are derived from the company facts in
`services/demo_agents/src/demo_agents/company.py`, so the gold answers and the knowledge-base
documents can never drift apart. A suite's dataset version is a hash of its content.
