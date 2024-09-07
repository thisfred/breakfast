.PHONY: test install install-dev lint dist-clean

test:
	uv run coverage run -m pytest
	uv run coverage report -m

lint:
	uv run pre-commit run --all-files
