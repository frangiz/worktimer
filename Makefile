.PHONY: all check format format-check install test

check:
	uv run ruff check main.py test_*

format:
	uv run ruff format main.py test_*

format-check:
	uv run ruff format --check main.py test_*

install:
	uv sync

test:
	uv run pytest --cov=main --cov-report=html --cov-report=term-missing

all: test format check