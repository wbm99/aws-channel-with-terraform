# syntax=docker/dockerfile:1
# One image with every tool the project needs; the project files come from a bind mount of the checkout at /work.
# Targets: `runtime` (published, no dev dependencies) and `test` (runtime plus pytest and moto, for CI only).

FROM hashicorp/terraform:1.16 AS terraform

FROM python:3.12-alpine AS runtime
# bash: the justfile and source/send-srt.sh need it. font-dejavu: the burned-in clock of the test source.
RUN apk add --no-cache bash ffmpeg font-dejavu just
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
COPY tools /tmp/tools
RUN pip install --no-cache-dir /tmp/tools && rm -rf /tmp/tools
# HOME is writable by any UID, because compose runs the container as the host user's UID (see compose.yaml).
RUN mkdir -p /home/app && chmod 1777 /home/app
# TF_DATA_DIR: provider binaries are built for one OS, so the container keeps its own next to the host's .terraform/.
ENV HOME=/home/app \
    LIVECTL=livectl \
    PYTHON=python \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FONT=/usr/share/fonts/dejavu/DejaVuSans.ttf \
    TF_DATA_DIR=.terraform-docker
WORKDIR /work
USER 1000:1000
EXPOSE 8765
CMD ["livectl", "ui", "--host", "0.0.0.0", "--container", "--no-browser"]

FROM runtime AS test
USER root
COPY tools /tmp/tools
RUN pip install --no-cache-dir "/tmp/tools[dev]" && rm -rf /tmp/tools
ENV PYTEST=pytest
USER 1000:1000
