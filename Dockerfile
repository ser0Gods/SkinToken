FROM nvidia/cuda:12.8.0-devel-ubuntu22.04

ARG SKINTOKENS_SHA=273b691d35989d71cd17ff2895fdc735097b92d1

ENV PATH="/root/.local/bin:/opt/.venv/bin:${PATH}" \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
       curl git build-essential ninja-build \
       libx11-6 libx11-xcb1 libxext6 libxrender1 libxi6 \
       libgl1 libegl1 libgles2 \
       libglib2.0-0 libfontconfig1 fontconfig libfreetype6 \
       libsm6 libice6 libxkbcommon0 \
 && curl -LsSf https://astral.sh/uv/install.sh | sh \
 && cd /opt \
 && uv python install 3.11 \
 && uv venv .venv --seed --python 3.11 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN git clone https://github.com/VAST-AI-Research/SkinTokens.git . \
 && git checkout --quiet "${SKINTOKENS_SHA}"

RUN python -m pip install torch==2.7.0 --extra-index-url https://download.pytorch.org/whl/cu128

RUN python -m pip install psutil numpy

RUN python -m pip install flash-attn --no-build-isolation

RUN python -m pip install -r requirements.txt

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
 && chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /app/input /app/results

VOLUME ["/app/experiments", "/app/models"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
