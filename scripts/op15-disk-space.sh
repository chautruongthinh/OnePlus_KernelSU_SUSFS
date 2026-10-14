#!/usr/bin/env bash
# Report the filesystem used by the job and stop before large stages if needed.
set -euo pipefail

workspace="${GITHUB_WORKSPACE:-$PWD}"
case "${1:-report}" in
  report)
    date -u '+%Y-%m-%dT%H:%M:%SZ'
    df -h "$workspace"
    df -B1 --output=size,used,avail,pcent,target "$workspace"
    df -i "$workspace"
    for directory in kernel/.repo kernel/prebuilts kernel/out artifacts; do
      if [ -d "$workspace/$directory" ]; then
        du -sh "$workspace/$directory"
      fi
    done
    ;;
  check)
    minimum_gib="${2:?minimum free GiB is required}"
    [[ "$minimum_gib" =~ ^[0-9]+$ ]]
    available_bytes="$(df -B1 --output=avail "$workspace" | tail -n 1)"
    printf 'Disk space %s: %s GiB free; need at least %s GiB\n' \
      "${3:-}" "$((available_bytes / 1024 / 1024 / 1024))" "$minimum_gib"
    df -h "$workspace"
    if (( available_bytes < minimum_gib * 1024 * 1024 * 1024 )); then
      echo '::error::Insufficient disk space. See disk-space.log; use a runner with more free disk space if cleanup is insufficient.'
      exit 1
    fi
    ;;
  *)
    echo "Usage: $0 report | check MINIMUM_GIB [STAGE]" >&2
    exit 2
    ;;
esac
