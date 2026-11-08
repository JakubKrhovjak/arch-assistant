#!/bin/bash
# The format of the assets list files (protected.txt, dependencies.txt), the one bash parser of it. Sourced by
# actions/_lib.sh and install.sh; defines a function only, no side effects. The Python twin is
# arch_assistant/protection.py read_list (tests/test_protection.py checks that both agree).

# list_file <file>: the entries of an assets list, one per line: # comments, whitespace and blank lines dropped.
list_file() {
    sed -e 's/#.*//' -e 's/[[:space:]]//g' -e '/^$/d' "$1"
}
