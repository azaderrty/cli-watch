#!/bin/bash
exec bash "$(dirname "$(readlink -f "$0")")/statuss.sh" "$@"
