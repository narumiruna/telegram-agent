#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

npm exec -- biome check --write --staged --no-errors-on-unmatched
git update-index --again
