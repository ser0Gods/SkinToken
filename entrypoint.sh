#!/bin/bash
set -euo pipefail
cd /app

CKPT="experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt"
if [ ! -f "$CKPT" ]; then
  echo "[skintokens] First run: downloading model checkpoints (persisted in named volumes)..."
  python download.py --model
fi

exec python demo.py --input /app/input --output /app/results --use_transfer ${EXTRA_ARGS:-}
