"""Secrets in free text: what the heuristic catches, what it lets through, and the policy that uses it."""
import pytest
from test_sidecar import REPO, denied, ok, world

from intentplane.textscan import secret_kinds

FAKE_GITHUB = "ghp_" + "FAKE0fake1FAKE2fake3FAKE4fake5FAKE6fa"   # the format of a GitHub token, not a token


@pytest.mark.parametrize(("text", "kind"), [
    (f"the key: {FAKE_GITHUB}", "github-token"),
    ("github_pat_" + "FAKE0fake1FAKE2fake3FAKE4", "github-token"),
    ("AKIA" + "FAKEFAKEFAKEFAKE", "aws-access-key"),
    ("xoxb-" + "0000000000-fakefakefake", "slack-token"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "private-key"),
    ("token=Zm9vYmFyMTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3Bx", "high-entropy"),
])
def test_known_formats_and_random_strings_are_found(text, kind):
    assert kind in secret_kinds(text)


@pytest.mark.parametrize("text", [
    "Fixed the typo in README (#1); PR #40.",
    "Merged at 0123456789abcdef0123456789abcdef01234567.",                           # a commit id
    "digest e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",       # a SHA-256
    "run 123e4567-e89b-12d3-a456-426614174000 failed",                              # a UUID
    "see https://github.com/demo/widgets/actions/runs/1234567890/job/9876543210",
    "ExecutionSettingsPanelWithAVeryLongComponentName2 renders twice",
    "test_merge_on_a_protected_base_without_review_needs_four_eyes",
    "FAKE-deploy-key-for-the-replay-only",                                           # words, not a token
])
def test_ordinary_text_passes(text):
    assert secret_kinds(text) == []


def test_the_gateway_refuses_to_publish_a_credential_on_the_tasks_own_thread():
    sc, gh = world()
    token = sc.issue("t1", "explicit", "fix-typo", threads=[12]).token
    out = sc.propose(token, "pr.comment", {"repo": REPO, "number": 12, "body": f"Deploy key for rotation: {FAKE_GITHUB}"})
    denied(out, "policy")
    assert "forbid-text-with-secret" in out["message"]
    out = sc.propose(token, "pr.create", {"repo": REPO, "head": "superagent/fix-typo", "base": "main",
                                          "title": f"key {FAKE_GITHUB}", "body": ""})
    denied(out, "policy")
    ok(sc.propose(token, "pr.comment", {"repo": REPO, "number": 12, "body": "Fixed the typo in #40."}))
    assert [w[0] for w in gh.writes] == ["comment"]
