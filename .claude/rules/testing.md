# Testing Rules

Every implementation task requires automated tests appropriate to its risk.

## Required Patterns

Use deterministic fixtures whenever possible.
Mock external LLM and market-data providers in unit tests.

LLM tests should cover:

- valid structured output
- malformed output
- missing evidence
- out-of-range scores
- retry/fallback behavior
- uncertain path

Financial analytics tests should use known numerical fixtures with expected outputs.

Point-in-time / no-look-ahead tests are mandatory for historical analytics and backtesting features.

Parser tests should use saved fixtures rather than live websites for CI stability.

## Completion

A task cannot be marked completed while required tests are failing.
Do not change expected values solely to accommodate incorrect code.
