# LiteLLM gateway — deployed as a Render web service.
# Render injects env vars (ANTHROPIC_API_KEY, REDIS_HOST, etc.) at runtime.
FROM ghcr.io/berriai/litellm:main-latest

COPY infra/litellm/config.yaml /app/config.yaml

# Render assigns PORT dynamically; LiteLLM binds to it.
ENV PORT=10000
EXPOSE 10000

CMD ["--config", "/app/config.yaml", "--port", "10000", "--host", "0.0.0.0"]
