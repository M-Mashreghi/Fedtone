# Architecture Rules

FedTone uses a modular, stage-oriented architecture.

## Core Flow

Official Central-Bank Source
→ Collector
→ Normalizer
→ Document Store
→ LLM Tone Analyzer
→ Evidence Validator
→ Statement Comparator
→ Market Data Engine
→ Event Study / Historical Analytics
→ API
→ Dashboard

## Boundaries

UI code must not implement:

- policy scoring logic
- market calculations
- source parsing
- persistence logic
- event-study logic

External providers should be accessed through adapters or explicit service boundaries.
Keep provider-specific parsing and API code out of core research logic.

## MVP Bias

Prefer a modular monolith over microservices for the initial product.
Keep SQLite acceptable for MVP/local use unless a task explicitly requires a stronger store.
Do not introduce queues, distributed caches, or orchestration platforms without a concrete requirement.
