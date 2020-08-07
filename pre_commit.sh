#!/bin/sh

set -e

black main.py test_main.py
isort --profile black main.py test_main.py
# Run twice so we first get the output if there is anything to do,
# then run again and fail if we had something to fix.
autoflake main.py test_main.py
autoflake --check main.py test_main.py
mypy main.py test_main.py

pytest --cov=. --cov-report html .

echo "All done :)"