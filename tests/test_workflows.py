"""Which GitHub Actions jobs would run a task's code with a repository secret (intentplane.connectors.workflows)."""
import pytest

from intentplane.connectors.workflows import exposed_jobs

BRANCH, PREFIX = "superagent/fix-typo", "superagent/"
TESTS_WITH_KEY = """
on: {on}
jobs:
  test:
    runs-on: ubuntu-latest
{guard}    env:
      DEPLOY_KEY: ${{{{ secrets.DEPLOY_KEY }}}}
    steps:
      - uses: actions/checkout@v4
      - run: npm test
"""


def wf(on: str = "[push, pull_request]", guard: str = "") -> dict[str, str]:
    return {".github/workflows/test.yml": TESTS_WITH_KEY.format(on=on, guard=f"    if: {guard}\n" if guard else "")}


def test_a_job_that_runs_the_branch_with_a_secret_is_exposed():
    assert exposed_jobs(wf(), BRANCH, PREFIX) == [".github/workflows/test.yml: test"]
    assert exposed_jobs(wf("pull_request"), BRANCH, PREFIX) == [".github/workflows/test.yml: test"]


@pytest.mark.parametrize("on, guard", [
    ("push", "${{ !startsWith(github.ref_name, 'superagent/') }}"),
    ("push", "\"!startsWith(github.ref, 'refs/heads/superagent/')\""),
    ("pull_request", "\"!startsWith(github.head_ref, 'superagent/')\""),
    ("[push, pull_request]", "\"!startsWith(github.ref_name, 'superagent/') && !startsWith(github.head_ref, 'superagent/')\""),
])
def test_a_job_kept_off_task_branches_for_each_event_that_runs_it_is_safe(on, guard):
    assert exposed_jobs(wf(on, guard), BRANCH, PREFIX) == []


@pytest.mark.parametrize("on, guard", [
    ("push", "\"!startsWith(github.head_ref, 'superagent/')\""),                  # empty on a push: the job still runs
    ("[push, pull_request]", "\"!startsWith(github.ref_name, 'superagent/')\""),  # a pull request's ref_name is N/merge
    ("push", "\"!startsWith(github.ref_name, 'superagent/') || true\""),
    ("push", "\"!startsWith(github.ref_name, 'other/')\""),
    ("workflow_run", "\"!startsWith(github.head_ref, 'superagent/')\""),
])
def test_a_guard_that_misses_an_event_or_can_be_widened_does_not_count(on, guard):
    assert exposed_jobs(wf(on, guard), BRANCH, PREFIX) == [".github/workflows/test.yml: test"]


@pytest.mark.parametrize("on", ["{push: {branches: [main]}}", "{push: {branches-ignore: ['superagent/**']}}",
                                "{push: {tags: ['v*']}}", "{push: {branches: ['**', '!superagent/**']}}",
                                "schedule", "workflow_dispatch"])
def test_a_workflow_that_never_runs_for_the_branch_is_safe(on):
    assert exposed_jobs(wf(on), BRANCH, PREFIX) == []


def test_filters_follow_githubs_patterns():
    assert exposed_jobs(wf("{push: {branches: ['superagent/*']}}"), BRANCH, PREFIX) != []
    assert exposed_jobs(wf("{push: {branches: ['superagent/*']}}"), "superagent/a/b", PREFIX) == []
    assert exposed_jobs(wf("{push: {branches: ['**']}}"), "superagent/a/b", PREFIX) != []


def test_secrets_reach_a_job_in_every_form():
    def job(body: str, shared: str = "") -> dict[str, str]:
        return {"w.yml": f"on: push\n{shared}jobs:\n  j:\n{body}"}
    assert exposed_jobs(job("    uses: org/repo/.github/workflows/deploy.yml@main\n    secrets: inherit\n"), BRANCH, PREFIX) != []
    assert exposed_jobs(job("    runs-on: x\n    steps: [{run: 'echo ok'}]\n", "env: {K: '${{ secrets.K }}'}\n"),
                        BRANCH, PREFIX) != []
    assert exposed_jobs(job("    runs-on: x\n    steps: [{run: 'echo ${{ toJSON(secrets) }}'}]\n"), BRANCH, PREFIX) != []
    assert exposed_jobs(job("    runs-on: x\n    steps: [{run: \"echo ${{ secrets['K'] }}\"}]\n"), BRANCH, PREFIX) != []
    # The workflow's own token is not a repository secret: its permissions are another matter (see the README).
    assert exposed_jobs(job("    runs-on: x\n    steps: [{env: {T: '${{ secrets.GITHUB_TOKEN }}'}, run: x}]\n"),
                        BRANCH, PREFIX) == []


def test_a_file_that_cannot_be_read_counts_as_exposed():
    assert exposed_jobs({"w.yml": "on: [push\n"}, BRANCH, PREFIX) is None
    assert exposed_jobs({"w.yml": "- just a list\n"}, BRANCH, PREFIX) is None
    assert exposed_jobs({"w.yml": "on: push\njobs:\n  j: run-this\n"}, BRANCH, PREFIX) is None
    assert exposed_jobs({}, BRANCH, PREFIX) == []
