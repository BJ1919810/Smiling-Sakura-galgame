#!/bin/bash
# 启动云卡 ComfyUI（幂等：已在跑就不重复起）
set -u
PORT=6006
if curl -s -o /dev/null -m 3 http://127.0.0.1:$PORT/system_stats; then
  echo "already_running"
  exit 0
fi
cd /root/autodl-tmp/ComfyUI || exit 1
nohup setsid /root/autodl-tmp/envs/comfy/bin/python main.py \
  --port $PORT --listen 0.0.0.0 --preview-method latent2rgb \
  > /root/autodl-tmp/comfy.log 2>&1 < /dev/null &
for i in $(seq 1 40); do
  sleep 2
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:$PORT/system_stats || true)
  if [ "$code" = "200" ]; then
    echo "started_in_$((i*2))s"
    curl -s http://127.0.0.1:$PORT/system_stats | head -c 400
    exit 0
  fi
done
echo "START_FAILED — 日志尾部："
tail -20 /root/autodl-tmp/comfy.log
exit 1
