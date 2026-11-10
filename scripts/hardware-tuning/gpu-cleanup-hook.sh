#!/bin/bash
# Proxmox LXC hook script for GPU cleanup
# Place at: /var/lib/vz/snippets/gpu-cleanup-hook.sh
# Activate with: pct set 109 -hookscript local:snippets/gpu-cleanup-hook.sh

VMID="$1"
PHASE="$2"

LOG="/var/log/lxc-gpu-hook-${VMID}.log"
exec >>"$LOG" 2>&1
echo "[$(date '+%F %T')] hook called: VMID=$VMID PHASE=$PHASE"

case "$PHASE" in
    pre-start)
        echo "  -> pre-start: ensure NVIDIA driver loaded and persistent"
        nvidia-smi -pm 1 >/dev/null 2>&1
        # Pre-create nvidia device nodes if missing
        nvidia-modprobe -c 0 -u 2>/dev/null
        # Lock GPU clocks for low latency (RTX 3060 Ti: max ~2100 MHz, memory 7001 MHz)
        nvidia-smi -lgc 0,2100 >/dev/null 2>&1 || true
        nvidia-smi -lmc 7001 >/dev/null 2>&1 || true
        # Ensure all nvidia devices are world-writable inside the CT
        # (must be AFTER nvidia-smi lock-commands, which reset nvidia-caps permissions)
        chmod 666 /dev/nvidia0 /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools 2>/dev/null
        [ -d /dev/nvidia-caps ] && chmod 666 /dev/nvidia-caps/nvidia-cap* 2>/dev/null
        # Show GPU state before start
        nvidia-smi --query-gpu=memory.used,memory.free,temperature.gpu --format=csv,noheader
        ;;

    post-start)
        echo "  -> post-start: CT $VMID started, GPU state:"
        # Re-ensure nvidia-caps are accessible (driver may reset perms during CT start)
        chmod 666 /dev/nvidia0 /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools 2>/dev/null
        [ -d /dev/nvidia-caps ] && chmod 666 /dev/nvidia-caps/nvidia-cap* 2>/dev/null
        nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
        ;;

    pre-stop)
        echo "  -> pre-stop: gracefully terminating GPU users in CT $VMID"
        # Try graceful shutdown of services first
        pct exec "$VMID" -- systemctl stop sunshine xfce-session xorg-display 2>/dev/null
        sleep 2
        ;;

    post-stop)
        echo "  -> post-stop: CT $VMID stopped, cleaning GPU state"
        # Kill any orphaned processes still holding nvidia devices
        for dev in /dev/nvidia0 /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools /dev/nvidia-caps/nvidia-cap1 /dev/nvidia-caps/nvidia-cap2; do
            holders=$(fuser "$dev" 2>/dev/null | tr -s ' ')
            if [ -n "$holders" ]; then
                echo "  ! Found orphaned holders of $dev: $holders"
                fuser -k "$dev" 2>/dev/null
            fi
        done
        sleep 1
        # Show final GPU state
        nvidia-smi --query-gpu=memory.used,memory.free,temperature.gpu --format=csv,noheader
        # Drop kernel caches on host (frees buffers used by CT rootfs)
        sync
        echo 1 > /proc/sys/vm/drop_caches
        echo "  -> GPU cleanup complete"
        ;;
esac

exit 0
