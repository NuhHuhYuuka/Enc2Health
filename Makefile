COMPOSE ?= docker compose

.PHONY: up down logs ps seed benchmark smoke
smoke-local:
	@echo "Running local (non-Docker) smoke test"
	AUTH_JWT_SECRET=$${AUTH_JWT_SECRET:-dev-secret-32-bytes-long-1234567890} \
	bash scripts/smoke_test_local.sh

up:
	$(COMPOSE) up --build

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

seed:
	$(COMPOSE) run --rm seeder

benchmark:
	AUTH_JWT_SECRET=$${AUTH_JWT_SECRET:-dev-secret-32-bytes-long-1234567890} python tests/benchmark.py

smoke:
	@echo "Running smoke test: generate certs, bring up stack, healthcheck and sample query"
	COMPOSE=$${COMPOSE:-$(COMPOSE)} \
	AUTH_JWT_SECRET=$${AUTH_JWT_SECRET:-dev-secret-32-bytes-long-1234567890} \
	bash scripts/smoke_test.sh