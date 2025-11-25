#!/bin/bash

poetry run pytest -vvvv --cov=jinjaturtle --cov-report=term-missing --disable-warnings
