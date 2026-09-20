#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

biome migrate --write
biome format --write
biome check --write
