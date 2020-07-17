#!/bin/sh

set -e

black main.py test_main.py
isort main.py test_main.py
mypy main.py test_main.py
#pytest --cov=app --cov-report html .
python -m pytest