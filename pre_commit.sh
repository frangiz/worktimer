#!/bin/sh

set -e

black main.py test_main.py test_main_bugfixes.py
isort --profile black main.py test_main.py test_main_bugfixes.py
# Run twice so we first get the output if there is anything to do,
# then run again and fail if we had something to fix.
autoflake main.py test_main.py test_main_bugfixes.py
autoflake --check main.py test_main.py test_main_bugfixes.py
mypy main.py test_main.py test_main_bugfixes.py

pytest --cov=. --cov-report html .

echo "All done :)"