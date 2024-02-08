## To do list

### Features:

* cache scope graphs (and/or partial paths?)
* add other refactorings
  * extract function/method
  * inline function/method
  * extract variable
* add go to definition
* refactor State into the graph where possible
* migrate to tox or nox, so we can test against all supported python
  versions locally as well as in github actions.
* refactor lines/guaranteed lines (use fields?)
* refactor rename to use Edit

### Bugs:

* rename visit in `@visit.register`
