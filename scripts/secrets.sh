#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
.local/bin/gitleaks git --redact --no-banner \
  --report-format template --report-template scripts/gitleaks-report.tmpl --report-path - "$@"
