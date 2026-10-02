#!/bin/sh
set -eu
exec /usr/local/bin/python3 -u /notifier/dualis_notifier.py "$@"
