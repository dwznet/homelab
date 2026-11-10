#!/bin/bash
# Codzienny SMART short self-test na wszystkich dyskach
LOG=/var/log/smart-daily.log
echo "=== $(date) ===" >> $LOG
for dev in $(smartctl --scan | awk '{print $1}'); do
    echo "--- $dev ---" >> $LOG
    # Uruchom short test
    smartctl -t short "$dev" >> $LOG 2>&1
    sleep 130  # czekaj 2 minuty na test
    # Wyniki
    smartctl -l selftest "$dev" | tail -5 >> $LOG 2>&1
    # Kluczowe atrybuty
    smartctl -A "$dev" | grep -E "Reallocated|Pending|Uncorrectable|CRC|Temperature|Wear|Power_On" >> $LOG 2>&1
    echo "" >> $LOG
done
# Obróć log (max 1MB)
if [ $(stat -c%s "$LOG" 2>/dev/null || echo 0) -gt 1048576 ]; then
    tail -1000 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
