.PHONY: test
test: .requirements
	tox

pip-tools:
	pip install pip-tools

.requirements: pip-tools requirements.txt test-requirements.txt
	pip install -r requirements.txt -r test-requirements.txt
	touch $@

%.txt: %.in pip-tools
	pip-compile -v --generate-hashes --output-file $@ $<
