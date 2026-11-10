#!/bin/bash
# Pin performance governor to CPUs used by sunshine-streaming CT 109
# Socket 1 cores: 18-25 (physical) + 54-61 (HT siblings) = 16 logical CPUs

# CPUs dedicated to streaming
STREAMING_CPUS="18 19 20 21 22 23 24 25 54 55 56 57 58 59 60 61"

for cpu in $STREAMING_CPUS; do
    if [ -w "/sys/devices/system/cpu/cpu${cpu}/cpufreq/scaling_governor" ]; then
        echo performance > "/sys/devices/system/cpu/cpu${cpu}/cpufreq/scaling_governor"
    fi
done

# Disable deep C-states for these CPUs (reduce wakeup latency)
for cpu in $STREAMING_CPUS; do
    for state in /sys/devices/system/cpu/cpu${cpu}/cpuidle/state[2-9]/disable; do
        [ -w "$state" ] && echo 1 > "$state" 2>/dev/null
    done
done

# Set scaling_min_freq to max for these CPUs (no downclocking)
for cpu in $STREAMING_CPUS; do
    max_freq=$(cat /sys/devices/system/cpu/cpu${cpu}/cpufreq/cpuinfo_max_freq 2>/dev/null)
    if [ -n "$max_freq" ] && [ -w "/sys/devices/system/cpu/cpu${cpu}/cpufreq/scaling_min_freq" ]; then
        echo "$max_freq" > "/sys/devices/system/cpu/cpu${cpu}/cpufreq/scaling_min_freq"
    fi
done

logger -t streaming-perf "Performance mode applied to CPUs: $STREAMING_CPUS"
exit 0
