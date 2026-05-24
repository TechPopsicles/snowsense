include .env
export

# ── dbt ──────────────────────────────────────────────────────────────────────
dbt-debug:
	cd dbt_project && dbt debug --profiles-dir .

dbt-compile:
	cd dbt_project && dbt compile --profiles-dir .

dbt-run:
	cd dbt_project && dbt run --profiles-dir .

dbt-test:
	cd dbt_project && dbt test --profiles-dir .

dbt-docs:
	cd dbt_project && dbt docs generate --profiles-dir . && dbt docs serve --profiles-dir .

# ── Docker ───────────────────────────────────────────────────────────────────
up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f agent

# ── pgvector index builders (run once, outside Docker) ───────────────────────
build-pgvector:
	PG_HOST=localhost python scripts/build_pgvector.py

build-fingerprints:
	PG_HOST=localhost python scripts/build_fingerprints.py

seed-fingerprints:
	PG_HOST=localhost python scripts/seed_fingerprints.py

# ── Shortcut: full setup from scratch ────────────────────────────────────────
setup: dbt-compile build-pgvector seed-fingerprints
	@echo "Setup complete. Run 'make up' to start all services."
	@echo "Optional: run 'make build-fingerprints' if you have ACCOUNTADMIN access to seed real query history."
