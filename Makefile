test-continually:
	rerun -i=.cache -i=.tox -i=.coverage -i=.venv --verbose tox
