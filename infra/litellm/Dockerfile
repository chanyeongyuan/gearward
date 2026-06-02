# LiteLLM gateway — deployed as a Render web service.
# Render injects env vars (ANTHROPIC_API_KEY, REDIS_HOST, etc.) at runtime.
FROM ghcr.io/berriai/litellm:main-latest

COPY infra/litellm/config.yaml /app/config.yaml

ENV PORT=10000
EXPOSE 10000

# Clear the base image ENTRYPOINT so we can use a shell wrapper for $PORT expansion.
ENTRYPOINT []
CMD ["sh", "-c", "litellm --config /app/config.yaml --port ${PORT:-10000} --host 0.0.0.0"]
