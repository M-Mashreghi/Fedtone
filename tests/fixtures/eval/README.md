# Evaluation datasets

`mini_benchmark.json` is a **synthetic** three-item set used to test the harness itself.
Its wording and labels are invented and say nothing about model quality.

A real benchmark needs human-labeled official FOMC statements in the same format:

```json
[{"id": "fomc-2024-01-31", "title": "...", "published_at": "2024-01-31T19:00:00+00:00",
  "paragraphs": ["...", "..."], "expected_label": "hawkish"}]
```

Run it with `fedtone benchmark --dataset <file> --repeats 3 --output reports/benchmark`.
