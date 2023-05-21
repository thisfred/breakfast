.PHONY: test dependencies install lint

test: .venv/installed
	.venv/bin/pytest

dependencies: requirements.txt test-requirements.txt optional-requirements.txt dev-requirements.txt

install: .venv/installed

lint: .venv/installed
	.venv/bin/pre-commit run --all-files

dist-clean:
	rm -rf .venv

.venv:
	python -m venv .venv
	.venv/bin/pip install pip-tools
	touch $@

.venv/installed: .venv requirements.txt test-requirements.txt optional-requirements.txt dev-requirements.txt
	.venv/bin/pip install -r requirements.txt -r test-requirements.txt -r dev-requirements.txt -r optional-requirements.txt
	.venv/bin/pip install -e .
	touch $@

%.txt: %.in .venv
	.venv/bin/pip-compile -v --generate-hashes --output-file $@ $<
