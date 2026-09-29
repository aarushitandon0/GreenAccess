# Committed LLM cache (MASTERSPEC §16)

Cached answers from the LLM for the Daily Herald demo run, so `LLM_OFFLINE=1`
can replay the AI-assisted fixes without network access. Every entry records
the model, the date it was produced and its token usage, and is labelled
`"cached": true` / "AI-generated, review before use".

Entries are keyed by a hash of model + prompts + image hashes + answer schema +
prompt version (`app/llm/cache.py`), so they are only ever served for exactly
the request that produced them. They are **never written by hand**.

To (re)record, with a real key:

    make demo-record     # ANTHROPIC_API_KEY set; writes here and to ../demo_scan_*.json

To replay offline:

    make demo-replay     # LLM_OFFLINE=1, answers served only from this folder

If this folder holds only this README, no keyed run has been recorded yet and
the demo's AI fixes fall back to page-context values or "manual fix needed".
