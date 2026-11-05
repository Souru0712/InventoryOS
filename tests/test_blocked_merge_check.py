"""Throwaway: proves the 'Protect main' ruleset blocks merging a pull request whose CI fails. Never merged."""


def test_this_branch_must_never_be_merged():
    assert False, "deliberate failure to prove branch protection"
