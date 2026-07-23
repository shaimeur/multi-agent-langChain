.PHONY: install test lint fmt api cli docker requirements goals

install:
	uv sync --extra dev

# Where the build plan says you are — the resume point, without opening the file.
goals:
	@sed -n '/^## Where I am/,/^---/p' GOALS.md

test:
	CACHE_MODE=replay uv run pytest

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

fmt:
	uv run ruff check --fix src tests
	uv run ruff format src tests

api:
	uv run uvicorn forge.api.main:app --reload

cli:
	uv run forge --help

# The cahier des charges asks for requirements.txt; pyproject stays the source
# of truth and this stays generated. Regenerate before submitting.
requirements:
	uv export --no-hashes --no-emit-project --extra dev -o requirements.txt

docker:
	docker compose up --build
