#!/bin/bash
# plan-finder daemon wrapper
# Usage: ./plan-finder-daemon.sh start [--at HH:MM] [-- plan-finder args...]
# Usage: ./plan-finder-daemon.sh stop
# Usage: ./plan-finder-daemon.sh status

PID_FILE="$HOME/.plan-finder-daemon.pid"
LOG_FILE="$HOME/.plan-finder-daemon.log"
ARGS_FILE="$HOME/.plan-finder-daemon.args"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

run_daemon() {
    echo $$ > "$PID_FILE"

    TARGET_TIME=""
    if [ -f "$HOME/.plan-finder-daemon.target-time" ]; then
        TARGET_TIME=$(cat "$HOME/.plan-finder-daemon.target-time")
        rm -f "$HOME/.plan-finder-daemon.target-time"
    fi

    CWD=""
    if [ -f "$HOME/.plan-finder-daemon.cwd" ]; then
        CWD=$(cat "$HOME/.plan-finder-daemon.cwd")
        rm -f "$HOME/.plan-finder-daemon.cwd"
    fi

    log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"; }

    if [ -n "$TARGET_TIME" ]; then
        log "Waiting until $TARGET_TIME to start..."
        while true; do
            NOW=$(date '+%H:%M')
            if [ "$NOW" = "$TARGET_TIME" ]; then
                break
            fi
            sleep 30
        done
    fi

    log "Starting plan-finder..."

    export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

    # Read args from file (one arg per line)
    PF_ARGS=()
    if [ -f "$ARGS_FILE" ]; then
        while IFS= read -r line; do
            PF_ARGS+=("$line")
        done < "$ARGS_FILE"
        rm -f "$ARGS_FILE"
    fi

    cd "${CWD:-$HOME}"
    uv run --project "$SCRIPT_DIR" plan-finder "${PF_ARGS[@]}" >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?

    log "plan-finder finished with exit code $EXIT_CODE"
    rm -f "$PID_FILE"
}

start_daemon() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Already running (PID $(cat "$PID_FILE")). Stop first: $0 stop"
        exit 1
    fi

    # Parse --at TIME
    TARGET_TIME=""
    shift  # remove 'start'
    if [ "$1" = "--at" ]; then
        TARGET_TIME="$2"
        shift 2
    fi

    # Skip '--' separator if present
    [ "$1" = "--" ] && shift

    # Save args to file (one per line) to avoid quoting issues
    printf '%s\n' "$@" > "$ARGS_FILE"

    # Save target time and cwd
    [ -n "$TARGET_TIME" ] && echo "$TARGET_TIME" > "$HOME/.plan-finder-daemon.target-time"
    pwd > "$HOME/.plan-finder-daemon.cwd"

    # Run in background
    nohup "$0" _run > /dev/null 2>&1 &

    sleep 1
    if [ -f "$PID_FILE" ]; then
        echo "Daemon started (PID $(cat "$PID_FILE"))"
        echo "Log: $LOG_FILE"
        [ -n "$TARGET_TIME" ] && echo "Will run at: $TARGET_TIME"
    else
        echo "Failed to start daemon"
    fi
}

stop_daemon() {
    if [ ! -f "$PID_FILE" ]; then
        echo "Not running"
        exit 0
    fi
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "Stopped (PID $PID)"
    else
        echo "Process $PID not found (already stopped)"
    fi
    rm -f "$PID_FILE"
}

status_daemon() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Running (PID $(cat "$PID_FILE"))"
        echo "Recent log:"
        tail -5 "$LOG_FILE" 2>/dev/null
    else
        echo "Not running"
    fi
}

case "$1" in
    start)  start_daemon "$@" ;;
    stop)   stop_daemon ;;
    status) status_daemon ;;
    _run)   run_daemon ;;
    *)
        echo "Usage: $0 {start|stop|status} [--at HH:MM] [-- plan-finder-args...]"
        echo ""
        echo "Examples:"
        echo "  $0 start -- --auto --prompt 'find improvements' --max 50"
        echo "  $0 start --at 02:59 -- --auto --prompt 'find improvements' --max 50"
        echo "  $0 stop"
        echo "  $0 status"
        ;;
esac
