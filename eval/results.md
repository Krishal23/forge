# Forge vs single-agent baseline

Mock mode: the baseline is the scripted developer's first attempt with no feedback loop, so this table shows which problems Forge's controls catch. It is not a model-quality benchmark; run `forge eval --mode live --runs 3` for that.

| Scenario | System | Hidden tests | Existing tests | Blocking security | High-risk files changed | Security checks removed | LLM calls | Est. cost |
|---|---|---|---|---|---|---|---|---|
| REQ-PAY-001 | Single agent | 1/12 | FAIL | 0 | payments/auth.py | assert_merchant_owns_payment | 0 | $0.0000 |
| REQ-PAY-001 | Forge | 12/12 | pass | 0 | none | none | 13 | $0.0248 |
| REQ-INV-001 | Single agent | 4/9 | pass | 0 | none | none | 0 | $0.0000 |
| REQ-INV-001 | Forge | 9/9 | pass | 0 | none | none | 8 | $0.0136 |
