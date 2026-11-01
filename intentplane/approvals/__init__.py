"""The approval service: step-up and four-eyes confirmations outside the agent's client.

When the policy requires a human, the gateway returns a pending operation and its hash. The approval board opens
a request showing the RESOLVED operation; an authenticated approver approves or rejects it in a web page; the
approval is signed over that exact operation hash, single use, and the gateway checks it again (step-up: the
principal; four eyes: someone else). Every request and decision is a hash-chained receipt.

The open-source proxy ships `LocalApprovals`: the user's own step-up, on a page served on this machine. Any
`ApprovalChannel` can replace it (more approvers, four eyes, single sign-on, a hosted service): see
docs/editions.md.
"""
from .board import FOUR_EYES, STEP_UP, ApprovalBoard, ApprovalRequest, hash_passcode
from .channel import ApprovalChannel, LocalApprovals

__all__ = ["FOUR_EYES", "STEP_UP", "ApprovalBoard", "ApprovalChannel", "ApprovalRequest", "LocalApprovals",
           "hash_passcode"]
