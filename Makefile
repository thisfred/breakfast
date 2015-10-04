ACTIVATE = .venv/bin/activate

REQS = pip install -Ur requirements.txt
TESTREQS = pip install -Ur test-requirements.txt
INSTALL = pip install -e '.'

venv: requirements.txt
	test -d .venv || virtualenv -p python3 .venv
	. $(ACTIVATE); $(REQS)
	. $(ACTIVATE); $(TESTREQS)
	. $(ACTIVATE); $(INSTALL)
	touch $(ACTIVATE)

pytest:
	. $(ACTIVATE); py.test -rf -l -s -x  --cov-report term-missing --doctest-glob=*.rst --cov breakfast

lint:
	. $(ACTIVATE); flake8 --max-complexity=10 breakfast tests

test: venv pytest lint

clean:
	rm -rf .venv
