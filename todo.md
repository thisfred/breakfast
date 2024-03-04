## To do list

### Features:

* cache scope graphs (and/or partial paths?)
* add go to definition
* add Undo
* add other refactorings
  * inline function
  * inline method
  * inline variable
* extract method
* extract function to top level scope

### Refactoring

* refactor State into the graph where possible
* refactor rename to use Edit

### Chores

* migrate to tox or nox, so we can test against all supported python
  versions locally as well as in github actions.

### Bugs:

* newline literals in source code seem to break Source.get_ast
* extract function will pass global names like classes and functions
