# Pricing and Usage Accounting

Agent Farm normalizes provider usage into five counters: input, cached input, cache-write input, output, and total tokens. Each model request also records a unique request ID, provider, model, latency, retry count, catalog version, matched price rule, price source, and estimated USD cost.

The built-in catalog is versioned by date. Its current entries were checked on 2026-08-02 against official provider pages:

- OpenAI: <https://openai.com/index/introducing-gpt-5-4/>
- Anthropic: <https://platform.claude.com/docs/en/about-claude/pricing>
- Google Gemini: <https://ai.google.dev/gemini-api/docs/pricing>
- DeepSeek: <https://api-docs.deepseek.com/quick_start/pricing/>

Pricing changes frequently. An unknown model intentionally has no estimated cost instead of inheriting a guessed price. Users can add contract, gateway, regional, or newly released prices through `model_price_overrides` in `agent-farm.local.json`:

```json
{
  "model_price_overrides": {
    "my-gateway/economy-*": {
      "input": 0.1,
      "cached_input": 0.02,
      "output": 0.4,
      "source": "internal contract 2026-08"
    }
  }
}
```

Rates are USD per one million tokens. Exact route keys take precedence over wildcard rules. Local Ollama and LM Studio inference is represented as zero API token cost; hardware and electricity costs are outside the current estimator.

Estimates are operational guidance, not invoices. Provider-side tool fees, tier multipliers, taxes, credits, peak pricing, and negotiated discounts may differ unless represented in an override.
