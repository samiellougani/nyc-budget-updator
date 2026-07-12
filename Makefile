.PHONY: build digest-dry digest-send test-sms

build:
	docker compose build

# Full pipeline, prints digest to stdout, no SMS, no state written
digest-dry: build
	docker compose run --rm digest python -m pipeline.main --dry-run

# Full pipeline, sends real SMS (uses TEST_PHONE_NUMBER from .env if set)
digest-send: build
	docker compose run --rm digest python -m pipeline.main

# Send a single test SMS to TEST_PHONE_NUMBER to verify Twilio wiring
test-sms: build
	docker compose run --rm digest python -m pipeline.main --test-sms
