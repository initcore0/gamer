.PHONY: install lint fmt type test check ci hooks up down migrate revision run

install:  ## install deps + pre-commit hooks
	uv sync --all-extras --dev
	uv run pre-commit install

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

type:
	uv run mypy

test:
	uv run pytest -m "not integration"

check: lint type test  ## everything CI runs (minus gitleaks)

ci: check
	uv run pre-commit run gitleaks --all-files

hooks:
	uv run pre-commit run --all-files

up:  ## boot the local stack (postgres + app)
	docker compose up --build

down:
	docker compose down

migrate:  ## apply migrations to the configured DB
	uv run alembic upgrade head

revision:  ## autogenerate a migration: make revision m="add x"
	uv run alembic revision --autogenerate -m "$(m)"

run:  ## run the app locally (needs a reachable postgres)
	uv run python -m gamer
