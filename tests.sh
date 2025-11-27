#!/bin/bash

set -eo pipefail

# Run pytests
poetry run pytest -vvvv --cov=jinjaturtle --cov-report=term-missing --disable-warnings

# Ensure we test the CLI like a human
for file in `ls -1 tests/samples/*`; do
  poetry run jinjaturtle -r test $file -d test.yml -t test.j2
done
