.PHONY: test install install-dev lint dist-clean

test: .venv/installed-test
	.venv/bin/coverage run .venv/bin/pytest
	.venv/bin/coverage report -m

install: .venv/installed

install-dev: .venv/installed-dev

lint: .venv/installed
	.venv/bin/pre-commit run --all-files

dist-clean:
	rm -rf .venv

.venv:
	python -m venv .venv
	touch $@

.venv/installed: .venv pyproject.toml
	.venv/bin/pip install -e .[test]
	touch $@

.venv/installed-dev: .venv pyproject.toml
	.venv/bin/pip install -e .[dev,lsp]
	touch $@

.venv/installed-test: .venv pyproject.toml
	.venv/bin/pip install -e .[test,lsp]
	touch $@
