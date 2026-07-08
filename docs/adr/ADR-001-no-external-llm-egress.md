# ADR-001: No External LLM Egress

## Status
Accepted

## Context
Survey data and portal DOM content may contain regulated or confidential information. Skyvern's reasoning loop must call an LLM, but that LLM cannot be a public SaaS endpoint.

## Decision
All LLM calls made by Skyvern, analyst-assist features, and any future agent services must target **Databricks Model Serving** (or another approved internally-hosted endpoint). The Skyvern deployment must be network-isolated from public LLM providers.

## Consequences
- Skyvern is configured with `OPENAI_API_BASE` pointing at the Databricks workspace.
- Egress firewall rules enforce the boundary at the network layer, not just at configuration.
- If a Databricks model endpoint is unavailable, Skyvern is unavailable. A runbook is required.
- Model choice is constrained to what Databricks serves; prompt engineering must accommodate those models.
