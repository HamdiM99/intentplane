"""The approval web app: a small, mobile-friendly page where an approver signs in, sees the RESOLVED operation and
approves or rejects it. Plain HTML forms, no JavaScript, no external assets.

Sessions are random tokens kept in memory (an HttpOnly, SameSite=Strict cookie); every form carries the session's
CSRF token; repeated wrong passcodes lock the approver out for a while. It listens on 127.0.0.1 by default: to
reach it from a phone, put it behind a TLS reverse proxy or tunnel and set `secure_cookies`.
"""
from __future__ import annotations

import hmac
import json
import secrets
import time
from html import escape

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from .board import STEP_UP, ApprovalBoard, ApprovalRequest

COOKIE = "intentplane_approver"
SESSION_TTL = 8 * 3600
MAX_FAILURES, LOCKOUT = 5, 300

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16191d;--muted:#5f6b7a;--line:#dde2e8;--ok:#1f7a4d;--no:#b42318;--accent:#2952cc}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--card:#1a1f25;--ink:#e8ebef;--muted:#9aa5b1;--line:#2c333b;
--ok:#3fb27f;--no:#f0705f;--accent:#7b9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,sans-serif}
main{max-width:640px;margin:0 auto;padding:16px}h1{font-size:1.25rem;margin:8px 0 16px}h2{font-size:1rem;margin:24px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:0 0 12px}
a.card{display:block;color:inherit;text-decoration:none}.muted{color:var(--muted);font-size:.875rem}
dl{display:grid;grid-template-columns:minmax(90px,auto) 1fr;gap:6px 12px;margin:8px 0}dt{color:var(--muted)}
dd{margin:0;overflow-wrap:anywhere;font-family:ui-monospace,monospace;font-size:.9rem}
.row{display:flex;gap:10px;flex-wrap:wrap}button{font:inherit;padding:12px 18px;border-radius:8px;border:0;
cursor:pointer;flex:1;min-width:120px}.approve{background:var(--ok);color:#fff}.reject{background:var(--no);color:#fff}
.link{background:none;color:var(--accent);padding:0;flex:none;min-width:0}input{font:inherit;width:100%;padding:10px;
border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);margin:4px 0 12px}
.badge{display:inline-block;font-size:.75rem;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}
.error{color:var(--no)}header{display:flex;justify-content:space-between;align-items:center}
"""


def _page(title: str, body: str, refresh: int | None = None) -> HTMLResponse:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return HTMLResponse(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
        f'content="width=device-width,initial-scale=1">{meta}<title>{escape(title)}</title><style>{CSS}</style>'
        f"</head><body><main>{body}</main></body></html>",
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"})


def _when(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""


def _who(r: ApprovalRequest) -> str:
    return f"confirmation by {r.principal}" if r.effect == STEP_UP else f"four eyes (not {r.principal})"


def _value(v) -> str:
    return escape(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str))


def create_app(board: ApprovalBoard, *, secure_cookies: bool = False) -> Starlette:
    sessions: dict[str, tuple[str, str, float]] = {}   # token -> (approver, csrf, expires)
    failures: dict[str, tuple[int, float]] = {}         # approver -> (count, locked until)

    def current(request: Request) -> tuple[str, str] | None:
        s = sessions.get(request.cookies.get(COOKIE, ""))
        if s is None or s[2] < time.time():
            return None
        return s[0], s[1]

    async def checked_form(request: Request, csrf: str):
        form = await request.form()
        return form if hmac.compare_digest(str(form.get("csrf", "")), csrf) else None

    def header(approver: str, csrf: str) -> str:
        return (f'<header><h1>intentplane approvals</h1><form method="post" action="/logout">'
                f'<input type="hidden" name="csrf" value="{csrf}"><button class="link">Sign out {escape(approver)}'
                f"</button></form></header>")

    async def login(request: Request) -> Response:
        error = ""
        if request.method == "POST":
            form = await request.form()
            approver, passcode = str(form.get("approver", "")).strip(), str(form.get("passcode", ""))
            count, until = failures.get(approver, (0, 0.0))
            if until > time.time():
                error = "Too many attempts. Try again later."
            elif board.authenticate(approver, passcode):
                failures.pop(approver, None)
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(16)
                sessions[token] = (approver, csrf, time.time() + SESSION_TTL)
                target = str(request.query_params.get("next", "/"))
                response = RedirectResponse(target if target.startswith("/requests/") else "/", status_code=303)
                response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="strict",
                                    secure=secure_cookies)
                return response
            else:
                count += 1
                failures[approver] = (0, time.time() + LOCKOUT) if count >= MAX_FAILURES else (count, 0.0)
                error = "Unknown approver or wrong passcode."
        nxt = escape(str(request.query_params.get("next", "")), quote=True)
        action = f"/login?next={nxt}" if nxt else "/login"
        return _page("Sign in", (
            f'<h1>intentplane approvals</h1><form class="card" method="post" action="{action}">'
            f'<p class="error">{escape(error)}</p><label>Approver<input name="approver" autocomplete="username" '
            f'required></label><label>Passcode<input name="passcode" type="password" autocomplete="current-password" '
            f'required></label><div class="row"><button class="approve">Sign in</button></div></form>'))

    async def logout(request: Request) -> Response:
        s = current(request)
        if s and await checked_form(request, s[1]) is not None:
            sessions.pop(request.cookies.get(COOKIE, ""), None)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    async def index(request: Request) -> Response:
        s = current(request)
        if s is None:
            return RedirectResponse("/login", status_code=303)
        approver, csrf = s
        pending = board.pending_for(approver)
        cards = "".join(
            f'<a class="card" href="/requests/{r.id}"><strong>{escape(r.action)}</strong> '
            f'<span class="badge">{escape(_who(r))}</span><div class="muted">for {escape(r.principal)}, '
            f"opened {_when(r.created)}</div></a>" for r in pending) or '<p class="muted">Nothing to approve.</p>'
        done = "".join(
            f'<div class="card"><strong>{escape(r.action)}</strong> <span class="badge">{escape(r.status)}</span>'
            f'<div class="muted">{escape(r.approver or "")} {_when(r.decided)}</div></div>'
            for r in board.history(20))
        return _page("Approvals", f"{header(approver, csrf)}<h2>Waiting for you</h2>{cards}"
                                  f"<h2>Recent</h2>{done or '<p class=muted>None yet.</p>'}", refresh=10)

    async def detail(request: Request) -> Response:
        s = current(request)
        rid = request.path_params["rid"]
        if s is None:
            return RedirectResponse(f"/login?next=/requests/{rid}", status_code=303)
        approver, csrf = s
        r = board.get(rid)
        if r is None:
            return _page("Not found", f'{header(approver, csrf)}<p class="card">No such request.</p>')
        rows = "".join(f"<dt>{escape(k)}</dt><dd>{_value(v)}</dd>" for k, v in r.shown.items())
        if r.status == "pending" and r.may_decide(approver):
            actions = (f'<form method="post" action="/requests/{r.id}/decide" class="row">'
                       f'<input type="hidden" name="csrf" value="{csrf}">'
                       f'<button class="approve" name="decision" value="approve">Approve</button>'
                       f'<button class="reject" name="decision" value="reject">Reject</button></form>')
        elif r.status == "pending":
            actions = f'<p class="muted">You cannot decide on this one: it needs {escape(_who(r))}.</p>'
        else:
            actions = (f'<p><span class="badge">{escape(r.status)}</span> {escape(r.approver or "")} '
                       f"{_when(r.decided)}</p>")
        return _page(r.action, (
            f'{header(approver, csrf)}<div class="card"><strong>{escape(r.action)}</strong> '
            f'<span class="badge">{escape(_who(r))}</span><p class="muted">The values below are the ones that will '
            f"be executed: critical ones come from trusted records or the mandate, never from the agent.</p>"
            f'<dl>{rows}</dl><p class="muted">Operation {escape(r.op_hash[:16])}… · requested by '
            f"{escape(r.principal)} · {_when(r.created)} · expires {_when(r.expires)}</p>{actions}</div>"
            f'<p><a href="/">All requests</a></p>'))

    async def decide(request: Request) -> Response:
        s = current(request)
        rid = request.path_params["rid"]
        if s is None:
            return RedirectResponse(f"/login?next=/requests/{rid}", status_code=303)
        approver, csrf = s
        form = await checked_form(request, csrf)
        if form is None or form.get("decision") not in ("approve", "reject"):
            return Response("bad request", status_code=400)
        try:
            board.decide(rid, approver, form.get("decision") == "approve")
        except PermissionError as e:
            return _page("Refused", f'{header(approver, csrf)}<p class="card error">{escape(str(e))}</p>'
                                    f'<p><a href="/">All requests</a></p>')
        return RedirectResponse(f"/requests/{rid}", status_code=303)

    return Starlette(routes=[
        Route("/", index), Route("/login", login, methods=["GET", "POST"]), Route("/logout", logout, methods=["POST"]),
        Route("/requests/{rid}", detail), Route("/requests/{rid}/decide", decide, methods=["POST"]),
    ])
