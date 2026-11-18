FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first so this layer is cached until pyproject.toml changes.
COPY pyproject.toml README.md ./
RUN mkdir app mock_shop && touch app/__init__.py mock_shop/__init__.py \
    && pip install . && pip uninstall -y nordic-price-aggregator

COPY alembic.ini ./
COPY app ./app
COPY mock_shop ./mock_shop
COPY benchmarks ./benchmarks

# Run as an unprivileged user.
RUN useradd --system --uid 10001 appuser
USER appuser

CMD ["python", "-m", "app.main"]
