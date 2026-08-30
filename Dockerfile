# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM python:3.13-slim

ARG UV_VERSION=0.12.7
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /code

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY ./pyproject.toml ./README.md ./uv.lock ./
COPY ./app ./app

RUN uv sync --frozen --no-dev --no-editable \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home app \
    && chown -R 10001:10001 /code

ARG AGENT_VERSION=0.1.0
ENV AGENT_VERSION=${AGENT_VERSION}

USER 10001:10001
EXPOSE 8080

CMD ["/code/.venv/bin/uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
