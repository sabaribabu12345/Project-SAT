# LLM and Model Routing

## Where LLMs live

| Surface | Model host | Purpose |
| --- | --- | --- |
| Skyvern reasoning loop | Databricks Model Serving (Llama 3 70B Instruct or DBRX Instruct) | Navigation, field mapping, readback extraction. |
| Optional analyst-assist: exception summary | Databricks Model Serving | Text only, no authority. |
| Optional analyst-assist: assessment draft | Databricks Model Serving | Text only, editable by analyst. |
| Optional internal RAG | Databricks Vector Search + Model Serving | Retrieval over internal playbooks. |

## Skyvern → Databricks wiring

Skyvern supports an OpenAI-compatible endpoint. Configure at deploy time:

```env
ENABLE_OPENAI=true
OPENAI_API_KEY=<databricks personal access token or service principal token>
OPENAI_API_BASE=https://<workspace-host>/serving-endpoints
OPENAI_DEFAULT_MODEL=databricks-claude-sonnet-4-6
LLM_KEY=OPENAI_GPT4O_MINI   # Skyvern's internal key name; the base URL above points it at Databricks
```

The Skyvern container must **not** have outbound network access to `api.openai.com`, `api.anthropic.com`, or any public LLM host. Enforce via egress firewall rules or by running inside a private subnet with a single route to the Databricks workspace.

## Forbidden configurations

- `OPENAI_API_BASE` pointing to `api.openai.com` in any environment that ever sees portal DOM content.
- Skyvern's vision models routed to public providers.
- Databricks tokens with more scope than the Model Serving endpoint.

## Allowed LLM use cases

1. Skyvern navigation and field mapping during fill.
2. Skyvern readback extraction during validate.
3. Drafting analyst-facing summaries of exceptions.
4. Drafting editable assessment explanations.
5. Retrieval over internal playbooks for analyst context.

## Forbidden LLM use cases

1. Deciding numeric field values.
2. Deciding whether a validation failure can be ignored.
3. Deciding whether a hold can be waived.
4. Releasing submit.
5. Any task that would require survey data to cross a network boundary.

## Model observability

- Skyvern's trace bundles (screenshots, DOM snapshots, model prompts/responses) are uploaded to a Unity Catalog Volume. These bundles can contain sensitive portal content; store them under an access-controlled schema and redact analyst-visible artifacts through the control plane UI.
- Databricks Model Serving request logs are retained per Databricks policy. Confirm retention policy before go-live with a real portal.
