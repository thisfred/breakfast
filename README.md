# breakfast

AST based refactoring tool for and written in Python. (Pretty early
days, somewhat usable.)

## Status

breakfast tries to refuse to guess in the face of ambiguity, so it
*should* leave code that it doesn't fully understand alone, but I would
definitely recommend using it only on code that is under version
control, and make sure to inspect the diffs it produces.

(Neo)Vim is the editor I'm using, so my focus is to be able to use
breakfast as a plugin there. Having said that, there is almost no vim
specific code in this repository, other than what is needed to be able
to install the project as a plugin from Github. Everything is
implemented as an LSP server, and thus should work with other editors
and IDEs that support that protocol.

I use Linux as my operating system of choice, and while I have tried
hard to not make too many assumptions about the OS, it's likely that
you'll encounter some issues if you try to use this project on Windows.
I'll happily accept bug reports and patches, and suggestions on how to
improve testing that will make stronger guarantees there.

## Installation

...

## Why 'breakfast'?


1. I don't know if it's the most important, but it's a good meal. Also
   it has AST in it.
2. breakfast and move things > move fast and break things

## Testing

To run tests:
(requires just to be installed, but you can always look at the commands
it runs in the `Justfile` file in the root directory and run them
directly.)

```
just test
```

To perform all checks:
(requires just to be installed, but you can always look at the commands
it runs in the `Justfile` file in the root directory and run them
directly.)

```
just check-all
