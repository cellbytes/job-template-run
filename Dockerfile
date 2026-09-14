FROM python:3.14-alpine
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

# Numeric UID so that Kubernetes can verify runAsNonRoot.
RUN addgroup -S app && adduser -S -G app -u 10001 app

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev

COPY controller.py /app/controller.py

RUN chown -R app:app /app
USER 10001

CMD ["kopf", "run", "--standalone", "--verbose", "controller.py"]
