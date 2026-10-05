# syntax=docker/dockerfile:1
#
# One image runs every Codebreakers process; the command selects the role:
#   docker run IMAGE                      -> HTTP API (default)
#   docker run IMAGE worker | relay       -> async analysis processes
#   docker run --entrypoint alembic IMAGE upgrade head   -> one-shot migration
#
# Base image is pinned by multi-arch index digest; Dependabot proposes bumps.
ARG PYTHON_IMAGE=python:3.12-slim-trixie@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

FROM ${PYTHON_IMAGE} AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /src

# Dependencies first so source edits do not invalidate this layer.
COPY requirements/build.txt requirements/runtime.txt requirements/
RUN pip install --require-hashes --no-deps -r requirements/build.txt \
 && python -m venv --without-pip /opt/venv \
 && pip --python /opt/venv/bin/python install \
        --require-hashes --no-deps -r requirements/runtime.txt

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip wheel --no-deps --no-build-isolation --wheel-dir /wheels . \
 && pip --python /opt/venv/bin/python install --no-deps /wheels/*.whl

FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="codebreakers" \
      org.opencontainers.image.description="Codebreakers cipher API, analysis worker, and outbox relay." \
      org.opencontainers.image.source="https://github.com/lteran9/Codebreakers" \
      org.opencontainers.image.licenses="MIT"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The runtime never installs packages, so drop pip and run as a fixed non-root UID.
RUN python -m pip uninstall --yes --root-user-action=ignore pip \
 && groupadd --system --gid 10001 codebreakers \
 && useradd --system --uid 10001 --gid codebreakers --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin codebreakers

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini ./
COPY migrations/ migrations/

USER 10001:10001
EXPOSE 8000

ENTRYPOINT ["codebreakers"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
