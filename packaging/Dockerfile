# syntax=docker/dockerfile:1
# sksidekick image: the Hermes fork's own image at the pinned tag, plus skmemory (its own venv, outside
# Hermes'), the hermes-skmemory provider as a bundled plugin, and sksidekick. Versions: packaging/versions.env.
# The base declares VOLUME /opt/data and uses it as HOME; the sidekick home is /opt/data/sidekick inside it.
ARG HERMES_IMAGE=sksidekick-hermes:build
FROM ${HERMES_IMAGE}

ARG SKMEMORY_VERSION
ARG HERMES_SKMEMORY_REF
ARG HERMES_SKMEMORY_SHA256
USER root

RUN set -eu; \
    test -n "${SKMEMORY_VERSION}"; \
    python3 -m venv /opt/skmemory; \
    /opt/skmemory/bin/pip install --no-cache-dir "skmemory==${SKMEMORY_VERSION}"; \
    ln -sf /opt/skmemory/bin/skmemory-mcp /usr/local/bin/skmemory-mcp; \
    ln -sf /opt/skmemory/bin/skmemory /usr/local/bin/skmemory

RUN set -eu; \
    test -n "${HERMES_SKMEMORY_REF}" && test -n "${HERMES_SKMEMORY_SHA256}"; \
    curl -fsSL -o /tmp/hsk.tgz "https://github.com/smilinTux/hermes-skmemory/archive/refs/tags/${HERMES_SKMEMORY_REF}.tar.gz"; \
    echo "${HERMES_SKMEMORY_SHA256}  /tmp/hsk.tgz" | sha256sum -c -; \
    mkdir -p /opt/hermes/plugins/memory/skmemory; \
    tar xzf /tmp/hsk.tgz -C /opt/hermes/plugins/memory/skmemory --strip-components=1 \
        --exclude='*/tests' --exclude='*/.github' --exclude='*/conftest.py' --exclude='*/pytest.ini'; \
    rm /tmp/hsk.tgz; \
    chmod -R a+rX,go-w /opt/hermes/plugins/memory/skmemory; \
    test -f /opt/hermes/plugins/memory/skmemory/plugin.yaml

COPY pyproject.toml README.md /src/sksidekick/
COPY src /src/sksidekick/src
RUN set -eu; \
    python3 -m venv /opt/sksidekick; \
    /opt/sksidekick/bin/pip install --no-cache-dir /src/sksidekick; \
    rm -rf /src

COPY --chmod=0755 packaging/sksidekick-shim.sh /usr/local/bin/sksidekick
COPY --chmod=0755 packaging/sidekick-hermes.sh /usr/local/bin/sidekick-hermes
COPY --chmod=0755 packaging/sidekick-start.sh /usr/local/bin/sidekick-start
COPY --chmod=0644 packaging/skmemory.json /opt/sksidekick/skmemory.json

ENV SKSIDEKICK_HOME=/opt/data/sidekick
# The first-run setup page (sidekick-start runs it until setup is done). Publish it on the host's
# loopback only: docker run -p 127.0.0.1:8765:8765 ...
EXPOSE 8765
# serve supervises the gateway; never let a bare `gateway run` move it under the fork's s6 profile-gateways.
ENV HERMES_GATEWAY_NO_SUPERVISE=1
CMD ["sidekick-start"]
