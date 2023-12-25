.PHONY: test coverage coverage-combine install lint dist-clean

test: .venv/installed
	.venv/bin/coverage run .venv/bin/pytest

coverage: .venv/installed
	.venv/bin/coverage report

coverage-combine: .venv/installed
	.venv/bin/coverage combine

install: .venv/installed

lint: .venv/installed
	.venv/bin/pre-commit run --all-files

dist-clean:
	rm -rf .venv

.venv:
	python -m venv .venv
	touch $@

.venv/installed: .venv pyproject.toml
	.venv/bin/pip install -e .[lsp,dev,test]
	touch $@
