.PHONY: build digest-dry digest-send test-discord

build:
	docker compose build

# Full pipeline, prints digest to stdout, no delivery, no state written
digest-dry: build
	docker compose run --rm digest python -m pipeline.main --dry-run

# Full pipeline: writes digest, posts to Discord, updates state
digest-send: build
	docker compose run --rm digest python -m pipeline.main

# Post a single test message to verify the Discord webhook wiring
test-discord: build
	docker compose run --rm digest python -m pipeline.main --test-discord
