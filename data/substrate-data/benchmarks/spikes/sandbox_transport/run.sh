set -euo pipefail
mkdir -p /run/host-secrets /run/sd /jail
head -c 32 /dev/urandom | base64 > /run/host-secrets/token
chmod 600 /run/host-secrets/token
python server.py /run/sd/flight.sock & sleep 1.5
cp /run/host-secrets/token /run/sd-token && chmod 644 /run/sd-token   # per-call token file for the jail
CG=/sys/fs/cgroup
nsjail -Mo -q --use_cgroupv2 --cgroupv2_mount $CG --cgroup_pids_max=64 \
  --cgroup_mem_max=$((1024*1024*1024)) --cgroup_mem_swap_max=0 -t 120 \
  --chroot /jail --cwd /tmp \
  -R /usr -R /lib -R /bin -R /etc/ssl -R /dev/urandom -R /dev/null \
  $( [ -e /lib64 ] && echo -R /lib64 ) \
  -R /spike/client.py:/client.py \
  -R /run/sd:/run/substrate-data -R /run/sd-token:/run/substrate-data/token \
  -T /tmp -E HOME=/tmp -E PATH=/usr/local/bin:/usr/bin:/bin -E LANG=C.UTF-8 -E PYTHONUNBUFFERED=1 \
  -- /usr/local/bin/python3 /client.py
