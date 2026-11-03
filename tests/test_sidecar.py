"""The sidecar: one gateway, many concurrent tasks of one agent host, one mandate per task (GitHub pack)."""
import json
from dataclasses import dataclass, field

import pytest

from intentplane import Authority
from intentplane.approvals.board import FOUR_EYES, STEP_UP, ApprovalBoard, hash_passcode
from intentplane.connectors.github import PullRequest, combine_ci, review_approved
from intentplane.sidecar import RequestError, Sidecar, SidecarConfig
from intentplane.sidecar.service import TaskHeld

REPO = "acme/app"
A, B, C, F = "a" * 40, "b" * 40, "c" * 40, "f" * 40


@dataclass
class FakeGitHub:
    pulls: dict = field(default_factory=dict)
    ci: dict = field(default_factory=dict)
    approved_prs: set = field(default_factory=set)
    writes: list = field(default_factory=list)

    def pull(self, repo, number):
        return self.pulls.get(int(number)) if repo == REPO else None

    def pulls_for_branch(self, repo, branch):
        return [p for p in self.pulls.values() if repo == REPO and p.head_ref == branch and p.state == "open"]

    def ci_status(self, repo, sha):
        return self.ci.get(sha, "unknown")

    def approved(self, repo, number):
        return number in self.approved_prs

    def create_pull(self, repo, head, base, title, body):
        self.writes.append(("create", repo, head, base, title))
        return {"number": 99}

    def comment(self, repo, number, body):
        self.writes.append(("comment", repo, number, body))
        return {"id": 1}

    def merge(self, repo, number, sha, method):
        self.writes.append(("merge", repo, number, sha, method))
        return {"merged": True}


class BoardChannel:
    """The approval board without its web page: tests decide as the people would."""

    def __init__(self, board):
        self.board, self.effects = board, board.effects

    def open(self, op_hash, intent, shown, effect):
        return self.board.open(op_hash, intent, shown, effect)

    def mark_executed(self, request_id):
        self.board.mark_executed(request_id)

    def link(self, request):
        return f"http://approvals/requests/{request.id}"


def world(channel=True, **cfg_kw):
    gh = FakeGitHub()
    gh.pulls = {
        40: PullRequest(40, "open", "superagent/fix-typo", REPO, A, "main"),        # task t1's PR
        41: PullRequest(41, "open", "superagent/other-task", REPO, B, "main"),      # task t2's PR
        42: PullRequest(42, "open", "superagent/fix-typo-dev", REPO, C, "dev"),     # task t3's PR, unprotected base
        50: PullRequest(50, "open", "superagent/fix-typo", "evil/app", F, "main"),  # a fork with t1's branch name
    }
    gh.ci = {A: "success", B: "success", C: "success", F: "success"}
    sc = Sidecar(SidecarConfig(operator="op", repo=REPO, protected=("main",), **cfg_kw), gh)
    if channel:
        sc.approvals.enroll("reviewer")
        board = ApprovalBoard(sc.approvals, {"op": hash_passcode("op-passcode"),
                                             "reviewer": hash_passcode("rv-passcode")},
                              effects=frozenset({STEP_UP, FOUR_EYES}))
        sc.channel = BoardChannel(board)
    return sc, gh


def ok(out):
    assert out["status"] == "executed", out
    return out


def denied(out, stage=None):
    assert out["status"] == "denied", out
    if stage:
        assert out["stage"] == stage, out
    return out


def merge_args(number=40, sha=A, method="squash"):
    return {"repo": REPO, "number": number, "head_sha": sha, "method": method}


# -- create and comment ----------------------------------------------------------------------------------------
def test_a_task_opens_a_pr_only_from_its_own_branch_onto_the_mandated_base():
    sc, gh = world()
    t = sc.issue("t1", "inferred", "fix-typo", threads=[12]).token
    args = {"repo": REPO, "head": "superagent/fix-typo", "base": "main", "title": "Fix typo", "body": "..."}
    ok(sc.propose(t, "pr.create", args))
    assert gh.writes == [("create", REPO, "superagent/fix-typo", "main", "Fix typo")]
    denied(sc.propose(t, "pr.create", {**args, "head": "superagent/other-task"}), "provenance")
    denied(sc.propose(t, "pr.create", {**args, "base": "release"}), "provenance")
    denied(sc.propose(t, "pr.create", {**args, "repo": "evil/app"}), "provenance")
    assert len(gh.writes) == 1


def test_a_task_comments_only_on_its_own_threads_and_prs():
    sc, gh = world()
    t = sc.issue("t1", "inferred", "fix-typo", threads=[12]).token
    ok(sc.propose(t, "pr.comment", {"repo": REPO, "number": 12, "body": "on it"}))        # the issue it came from
    ok(sc.propose(t, "pr.comment", {"repo": REPO, "number": "#40", "body": "ready"}))     # its own PR
    for other in (41, 50, 999, 7):   # another task's PR, a fork with the same branch name, unknown, unrelated
        denied(sc.propose(t, "pr.comment", {"repo": REPO, "number": other, "body": "x"}), "provenance")
    assert [w[2] for w in gh.writes] == [12, 40]


def test_the_agent_cannot_smuggle_anything_into_a_proposal():
    sc, _ = world()
    t = sc.issue("t1", "explicit", "fix-typo").token
    denied(sc.propose(t, "pr.comment", {"repo": REPO, "number": 40, "body": "x", "strength": "explicit"}), "contract")
    denied(sc.propose(t, "shell.run", {"cmd": "rm -rf /"}), "contract")
    denied(sc.propose(t, "pr.comment", ["not", "a", "mapping"]), "contract")
    denied(sc.propose("forged." + "0" * 64, "pr.comment", {"repo": REPO, "number": 40, "body": "x"}), "authn")


# -- merge -----------------------------------------------------------------------------------------------------
def test_an_inferred_task_never_merges():
    sc, gh = world()
    t = sc.issue("t1", "inferred", "fix-typo").token
    denied(sc.propose(t, "pr.merge", merge_args()), "delegation")
    with pytest.raises(ValueError):
        SidecarConfig(operator="op", repo=REPO, scopes={"explicit": frozenset(), "inferred": frozenset({"pr.merge"})})
    assert gh.writes == []


def test_the_merge_policy_forbids_an_inferred_task_even_if_it_held_the_scope():
    """Defense in depth: the Cedar policy reads the task's strength from the signed mandate."""
    sc, _ = world()
    token = sc.authority.issue("op", {"pr.merge"}, {"repo": REPO, "tenant": f"{REPO}@superagent/fix-typo",
                                                    "strength": "inferred"})
    out = denied(sc.propose(token, "pr.merge", merge_args()), "policy")
    assert "forbid-merge-inferred-task" in out["reasons"]


def test_merge_on_a_protected_base_without_review_needs_four_eyes():
    sc, gh = world()
    t = sc.issue("t1", "explicit", "fix-typo").token
    out = sc.propose(t, "pr.merge", merge_args())
    assert out["status"] == "pending" and out["effect"] == FOUR_EYES
    assert out["shown"]["head_sha"].startswith(A) and "branch_heads" in out["shown"]["head_sha"]
    request_id = out["approval_url"].rsplit("/", 1)[1]
    with pytest.raises(PermissionError):
        sc.channel.board.decide(request_id, "op", True)          # the operator is not a second person
    assert sc.propose(t, "pr.merge", merge_args())["status"] == "pending"
    sc.channel.board.decide(request_id, "reviewer", True)
    ok(sc.propose(t, "pr.merge", merge_args()))
    assert gh.writes == [("merge", REPO, 40, A, "squash")]
    # the approval was single use, and bound to that exact operation
    assert sc.propose(t, "pr.merge", merge_args(method="merge"))["status"] == "pending"
    assert len(gh.writes) == 1


def test_merge_with_an_approved_review_needs_the_operator_only():
    sc, gh = world()
    gh.approved_prs.add(40)
    t = sc.issue("t1", "explicit", "fix-typo").token
    out = sc.propose(t, "pr.merge", merge_args())
    assert out["status"] == "pending" and out["effect"] == STEP_UP
    sc.channel.board.decide(out["approval_url"].rsplit("/", 1)[1], "op", True)
    ok(sc.propose(t, "pr.merge", merge_args()))


def test_merge_on_an_unprotected_base_still_needs_the_operator():
    sc, _ = world()
    t = sc.issue("t3", "explicit", "fix-typo-dev").token
    out = sc.propose(t, "pr.merge", merge_args(42, C))
    assert out["status"] == "pending" and out["effect"] == STEP_UP


def test_no_merge_on_red_ci_or_a_commit_that_is_not_the_head():
    sc, gh = world()
    gh.approved_prs.add(40)
    t = sc.issue("t1", "explicit", "fix-typo").token
    for status in ("failure", "pending", "unknown"):
        gh.ci[A] = status
        assert "forbid-merge-ci-not-green" in denied(sc.propose(t, "pr.merge", merge_args()), "policy")["reasons"]
    gh.ci[A] = "success"
    denied(sc.propose(t, "pr.merge", merge_args(sha=B)), "provenance")   # another task's head
    denied(sc.propose(t, "pr.merge", merge_args(41, B)), "provenance")   # another task's PR
    denied(sc.propose(t, "pr.merge", merge_args(50, F)), "provenance")   # the fork
    assert gh.writes == []


def test_no_merge_if_the_head_moved_between_two_reads_of_github():
    """The registry still lists the old head while the PR already points to a new commit (a push in between)."""
    sc, gh = world()
    gh.approved_prs.add(40)
    t = sc.issue("t1", "explicit", "fix-typo").token
    stale = list(gh.pulls_for_branch(REPO, "superagent/fix-typo"))
    gh.pulls_for_branch = lambda repo, branch: [p for p in stale if p.head_ref == branch]
    gh.pulls[40] = PullRequest(40, "open", "superagent/fix-typo", REPO, C, "main")
    out = denied(sc.propose(t, "pr.merge", merge_args()), "policy")
    assert "forbid-merge-stale-head" in out["reasons"]


def test_without_an_approval_channel_anything_needing_a_person_is_refused():
    sc, gh = world(channel=False)
    t = sc.issue("t1", "explicit", "fix-typo").token
    denied(sc.propose(t, "pr.merge", merge_args()), "approval")
    assert gh.writes == []


# -- mandates --------------------------------------------------------------------------------------------------
def test_withdrawing_one_task_leaves_the_others_running():
    sc, _ = world()
    t1 = sc.issue("t1", "explicit", "fix-typo", threads=[12]).token
    t2 = sc.issue("t2", "explicit", "other-task").token
    assert sc.withdraw("t1", "stopped by a person")
    denied(sc.propose(t1, "pr.comment", {"repo": REPO, "number": 12, "body": "x"}), "withdrawn")
    ok(sc.propose(t2, "pr.comment", {"repo": REPO, "number": 41, "body": "x"}))   # same principal, still valid
    assert not sc.withdraw("t1")


def test_a_held_task_gets_no_mandate_until_a_person_releases_it(tmp_path):
    holds = tmp_path / "holds.json"
    sc, _ = world(holds=holds)
    token = sc.issue("t1", "explicit", "fix-typo", threads=[12]).token
    assert sc.withdraw("t1", "incident 42", hold=True)
    out = sc.propose(token, "pr.comment", {"repo": REPO, "number": 12, "body": "x"})
    denied(out, "withdrawn")
    assert out["held"] is True and "do not retry" in out["message"]
    with pytest.raises(TaskHeld, match="incident 42"):
        sc.issue("t1", "explicit", "fix-typo", threads=[12])
    # The hold survives a restart: a new sidecar (new signing key, every old mandate now `authn`) still refuses.
    restarted, _ = world(holds=holds)
    denied(restarted.propose(token, "pr.comment", {"repo": REPO, "number": 12, "body": "x"}), "authn")
    with pytest.raises(TaskHeld):
        restarted.issue("t1", "explicit", "fix-typo", threads=[12])
    assert restarted.release("t1") and not restarted.release("t1")
    ok(restarted.propose(restarted.issue("t1", "explicit", "fix-typo", threads=[12]).token, "pr.comment",
                         {"repo": REPO, "number": 12, "body": "x"}))
    events = [e["data"].get("event") for e in sc.ledger.entries + restarted.ledger.entries]
    assert events.count("task.held") == 1 and events.count("task.released") == 1


def test_a_task_can_be_held_before_it_holds_a_mandate():
    sc, _ = world()
    assert sc.withdraw("t9", "stop before it starts", hold=True)
    with pytest.raises(TaskHeld):
        sc.issue("t9", "explicit", "fix-typo")


def test_a_replaced_mandate_is_not_a_withdrawn_one():
    sc, _ = world()
    first = sc.issue("t1", "explicit", "fix-typo", threads=[12]).token
    sc.issue("t1", "explicit", "fix-typo", threads=[12])   # resume: the host issues again
    denied(sc.propose(first, "pr.comment", {"repo": REPO, "number": 12, "body": "x"}), "authn")


def test_reissuing_a_task_replaces_its_mandate():
    sc, _ = world()
    old = sc.issue("t1", "explicit", "fix-typo", threads=[12]).token
    new = sc.issue("t1", "explicit", "fix-typo", threads=[12, 13]).token
    denied(sc.propose(old, "pr.comment", {"repo": REPO, "number": 12, "body": "x"}), "authn")
    ok(sc.propose(new, "pr.comment", {"repo": REPO, "number": 13, "body": "x"}))


@pytest.mark.parametrize("kw", [
    {"task": "t 1"}, {"strength": "auto"}, {"short_name": "../main"}, {"short_name": "-x"},
    {"threads": [0]}, {"threads": ["12"]}, {"threads": [True]}, {"rules": "copy"},
])
def test_the_host_cannot_issue_a_malformed_mandate(kw):
    sc, _ = world()
    args = {"task": "t1", "strength": "explicit", "short_name": "fix-typo", "threads": [], "rules": [], **kw}
    with pytest.raises(RequestError):
        sc.issue(**args)


def test_the_call_budget_is_per_mandate():
    sc, _ = world(max_calls=2)
    t = sc.issue("t1", "inferred", "fix-typo", threads=[12]).token
    for _ in range(2):
        ok(sc.propose(t, "pr.comment", {"repo": REPO, "number": 12, "body": "x"}))
    denied(sc.propose(t, "pr.comment", {"repo": REPO, "number": 12, "body": "x"}), "budget")


def test_receipts_tie_every_decision_to_its_task():
    sc, _ = world()
    issued = sc.issue("t1", "inferred", "fix-typo", threads=[12])
    sc.propose(issued.token, "pr.comment", {"repo": REPO, "number": 12, "body": "x"})
    sc.withdraw("t1", "done")
    events = [e["data"] for e in sc.ledger.entries]
    assert events[0]["event"] == "mandate.issued" and events[0]["task"] == "t1" and events[0]["jti"] == issued.jti
    assert events[1]["status"] == "EXECUTING" and events[1]["intent"]["mandate"] == issued.jti
    assert events[-1]["event"] == "mandate.withdrawn"
    assert sc.ledger.verify()


def test_revoking_one_token_keeps_the_principals_other_mandates():
    auth = Authority()
    a, b = auth.issue("op", {"x"}), auth.issue("op", {"x"})
    auth.revoke_token(auth.verify(a).jti)
    assert auth.verify(a) is None and auth.verify(b) is not None


# -- facts read from GitHub --------------------------------------------------------------------------------------
def test_ci_and_reviews_are_read_conservatively():
    assert combine_ci([], []) == "unknown"
    assert combine_ci([{"status": "completed", "conclusion": "success"}], []) == "success"
    assert combine_ci([{"status": "completed", "conclusion": "skipped"}], [{"state": "success"}]) == "success"
    assert combine_ci([{"status": "in_progress"}], []) == "pending"
    assert combine_ci([{"status": "completed", "conclusion": "failure"}], [{"state": "success"}]) == "failure"
    assert combine_ci([], [{"state": "error"}]) == "failure"
    u = lambda who, state: {"user": {"login": who}, "state": state}
    assert review_approved([u("a", "APPROVED")])
    assert not review_approved([u("a", "APPROVED"), u("b", "CHANGES_REQUESTED")])
    assert review_approved([u("a", "CHANGES_REQUESTED"), u("a", "APPROVED")])
    assert not review_approved([u("a", "APPROVED"), u("a", "DISMISSED")])
    assert not review_approved([u("a", "COMMENTED")])


# -- HTTP --------------------------------------------------------------------------------------------------------
SECRET = "s" * 40


@pytest.fixture
def client():
    from starlette.testclient import TestClient

    from intentplane.sidecar.app import create_app
    sc, gh = world(channel=False)
    with TestClient(create_app(sc, SECRET)) as c:
        yield c, gh


def test_http_only_the_host_issues_and_withdraws(client):
    c, _ = client
    body = {"task": "t1", "strength": "explicit", "short_name": "fix-typo", "threads": [12]}
    assert c.post("/v1/mandates", json=body).status_code == 401
    assert c.post("/v1/mandates", json=body, headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401
    r = c.post("/v1/mandates", json=body, headers={"Authorization": f"Bearer {SECRET}"})
    assert r.status_code == 201 and r.json()["branch"] == "superagent/fix-typo"
    token = r.json()["token"]
    # the task's own mandate is not the host's credential
    assert c.post("/v1/mandates", json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert c.delete("/v1/mandates/t1", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    bad = c.post("/v1/mandates", json={**body, "scopes": ["pr.merge"]}, headers={"Authorization": f"Bearer {SECRET}"})
    assert bad.status_code == 400
    assert c.delete("/v1/mandates/t1", headers={"Authorization": f"Bearer {SECRET}"}).json() == {"withdrawn": True,
                                                                                                    "held": False}


def test_http_hold_and_release(client):
    c, _ = client
    host = {"Authorization": f"Bearer {SECRET}"}
    body = {"task": "t1", "strength": "explicit", "short_name": "fix-typo", "threads": [12]}
    token = c.post("/v1/mandates", json=body, headers=host).json()["token"]
    assert c.request("DELETE", "/v1/mandates/t1", json={"hold": "yes"}, headers=host).status_code == 400
    r = c.request("DELETE", "/v1/mandates/t1", json={"reason": "incident", "hold": True}, headers=host)
    assert r.status_code == 200 and r.json() == {"withdrawn": True, "held": True}
    out = c.post("/v1/propose", json={"action": "pr.comment", "args": {"repo": REPO, "number": 12, "body": "x"}},
                 headers={"Authorization": f"Bearer {token}"}).json()
    assert out["stage"] == "withdrawn"
    r = c.post("/v1/mandates", json=body, headers=host)
    assert r.status_code == 409 and r.json()["held"] is True
    assert c.post("/v1/mandates/t1/release", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert c.post("/v1/mandates/t1/release", headers=host).json() == {"released": True}
    assert c.post("/v1/mandates/t1/release", headers=host).status_code == 404
    assert c.post("/v1/mandates", json=body, headers=host).status_code == 201


def test_http_propose(client):
    c, gh = client
    token = c.post("/v1/mandates", json={"task": "t1", "strength": "inferred", "short_name": "fix-typo",
                                         "threads": [12]}, headers={"Authorization": f"Bearer {SECRET}"}).json()["token"]
    call = {"action": "pr.comment", "args": {"repo": REPO, "number": 12, "body": "on it"}}
    assert c.post("/v1/propose", json=call).status_code == 401
    assert c.post("/v1/propose", json=call, headers={"Authorization": f"Bearer {token}"}).json()["status"] == "executed"
    r = c.post("/v1/propose", json={**call, "args": {**call["args"], "number": 41}},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["stage"] == "provenance"
    assert c.post("/v1/propose", content=b"{", headers={"Authorization": f"Bearer {token}"}).status_code == 400
    assert c.get("/v1/receipts/verify", headers={"Authorization": f"Bearer {SECRET}"}).json()["ok"]
    assert sorted(c.get("/v1/health").json()["actions"]) == ["pr.comment", "pr.create", "pr.merge"]
    assert gh.writes == [("comment", REPO, 12, "on it")]


TOKEN_TEXT = "Done. For the record the deploy key is ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def test_a_message_carrying_a_secret_is_refused_and_its_receipt_keeps_no_text(tmp_path):
    sc, _ = world(holds=tmp_path / "holds.json")
    assert sc.screen_message("t1", "github", "acme/app#12", "reply", "Fixed in #40, ready for review.") == {"status": "allowed"}
    out = sc.screen_message("t1", "feishu", "oc_123", "reply", TOKEN_TEXT)
    assert out["status"] == "denied" and out["stage"] == "message" and "github-token" in out["message"]
    last = sc.ledger.entries[-1]["data"]
    assert last["event"] == "message.screened" and last["status"] == "DENIED" and last["length"] == len(TOKEN_TEXT)
    assert "ghp_" not in json.dumps(sc.ledger.entries)
    assert sc.ledger.verify()


def test_a_held_task_sends_no_message():
    sc, _ = world()
    sc.withdraw("t1", "incident 42", hold=True)
    out = sc.screen_message("t1", "github", "acme/app#12", "reply", "All good, merging soon.")
    assert out["status"] == "denied" and "incident 42" in out["message"]
    assert sc.screen_message(None, "github", "acme/app#12", "notice", "All good.")["status"] == "allowed"


@pytest.mark.parametrize("args", [("t 1", "github", "x", "reply", "hi"), ("t1", "", "x", "reply", "hi"),
                                  ("t1", "github", "", "reply", "hi"), ("t1", "github", "x", "dm", "hi"),
                                  ("t1", "github", "x", "reply", 3), ("t1", "github", "x", "reply", "a" * 100_001)])
def test_the_host_cannot_screen_a_malformed_message(args):
    with pytest.raises(RequestError):
        world()[0].screen_message(*args)


def test_http_only_the_host_screens_messages(client):
    c, _ = client
    body = {"task": "t1", "source": "github", "conversation": "acme/app#12", "kind": "reply", "text": TOKEN_TEXT}
    assert c.post("/v1/messages/screen", json=body).status_code == 401
    token = c.post("/v1/mandates", json={"task": "t1", "strength": "explicit", "short_name": "fix-typo"},
                   headers={"Authorization": f"Bearer {SECRET}"}).json()["token"]
    assert c.post("/v1/messages/screen", json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 401
    host = {"Authorization": f"Bearer {SECRET}"}
    assert c.post("/v1/messages/screen", json=body, headers=host).json()["status"] == "denied"
    assert c.post("/v1/messages/screen", json={**body, "text": "ready"}, headers=host).json() == {"status": "allowed"}
    assert c.post("/v1/messages/screen", json={**body, "to": "x"}, headers=host).status_code == 400


def test_http_refuses_a_weak_host_secret():
    from intentplane.sidecar.app import create_app
    with pytest.raises(ValueError):
        create_app(world()[0], "short")


class ApiStub:
    """`GhCli` with its `gh api` calls answered from a table: path -> JSON, or missing (gh fails)."""

    def __init__(self, routes: dict, **kw):
        from intentplane.connectors.github import GhCli
        self.cli = GhCli(**kw)
        self.cli._api = lambda path, method="GET", body=None: self._answer(path, routes)

    @staticmethod
    def _answer(path, routes):
        if path not in routes:
            raise RuntimeError(f"gh api {path} failed")
        return routes[path]


def reviews_world(*reviews, permissions):
    review = lambda who, kind="User": {"user": {"login": who, "type": kind}, "state": "APPROVED"}
    routes = {f"repos/{REPO}/pulls/40": {"user": {"login": "agent-bot"}},
              f"repos/{REPO}/pulls/40/reviews?per_page=100": [review(*r) if isinstance(r, tuple) else review(r) for r in reviews]}
    routes.update({f"repos/{REPO}/collaborators/{who}/permission": {"permission": p} for who, p in permissions.items()})
    return ApiStub(routes, not_reviewers=frozenset({"Other-Agent"})).cli


def test_only_a_person_with_write_access_who_is_not_an_agent_counts_as_a_reviewer():
    permissions = {"outsider": "read", "worker-reader": "read", "agent-bot": "write", "other-agent": "write",
                   "ci-app[bot]": "write", "maintainer": "write"}
    # Each of these approvals is shown on the PR; none is a reviewer's.
    for who in ("outsider", "worker-reader", "agent-bot", "other-agent", ("ci-app[bot]", "Bot"), "ghost"):
        assert not reviews_world(who, permissions=permissions).approved(REPO, 40), who
    # Before: any approval counted, so an outsider's turned four eyes into the operator's confirmation alone.
    assert review_approved([{"user": {"login": "outsider"}, "state": "APPROVED"}])
    assert reviews_world("outsider", "maintainer", permissions=permissions).approved(REPO, 40)
    assert reviews_world("maintainer", permissions={**permissions, "maintainer": "triage"}).approved(REPO, 40) is False


def test_the_github_token_file_is_read_on_every_call(tmp_path, monkeypatch):
    """A GitHub App token lasts an hour: whatever renews the file, the next call uses the new token, and an empty
    file fails the call rather than falling back to another identity."""
    import subprocess as sp

    from intentplane.connectors.github import GhCli
    seen = []
    monkeypatch.setattr(sp, "run", lambda cmd, **kw: seen.append(kw["env"].get("GH_TOKEN")) or
                        sp.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr=""))
    token = tmp_path / "gh_token"
    cli = GhCli(token_file=str(token), env={"GH_TOKEN": "ambient"})
    token.write_text("ghs_first\n")
    cli._api("rate_limit")
    token.write_text("ghs_renewed\n")
    cli._api("rate_limit")
    assert seen == ["ghs_first", "ghs_renewed"]
    token.write_text("")
    with pytest.raises(RuntimeError, match="holds no token"):
        cli._api("rate_limit")
