#!/bin/bash
#
# entrypoint.sh — QuoteHub Docker entrypoint
#
# Fixes ownership of the data volume at startup (handles both fresh and
# pre-existing volumes), then drops privileges to the `quodb` user and
# runs the application.
#
# This is the standard Docker pattern:
#   1. Run as root (entrypoint)
#   2. chown the runtime volume so the app user can write to it
#   3. exec the CMD as the app user
#
set -e

# Fix ownership of the persistent data volume.
# On a fresh volume this is a no-op (files are already owned by quodb:quodb
# from the Dockerfile build step). On an existing volume created by a previous
# version that ran as root, this fixes ownership so the non-root process can
# write to it.
chown -R quodb:quodb /app/data

# Drop privileges and run the application
exec gosu quodb "$@"
