"""HTTP surface of the sidecar (Starlette). Bind it to 127.0.0.1 or a private socket only.

Host routes (Authorization: Bearer <INTENTPLANE_SIDECAR_SECRET>):
    POST   /v1/mandates            {task, strength, short_name, threads?, rules?, requester?} -> {token, branch, ...}
    DELETE /v1/mandates/{task}     {reason?, hold?}      hold: no new mandate until released
    POST   /v1/mandates/{task}/release                    lets a held task receive mandates again
    GET    /v1/receipts/verify
Agent route (Authorization: Bearer <the task's mandate>):
    POST   /v1/propose             {action, args}
Open:
    GET    /v1/health
"""
from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from intentplane import __version__

from .service import RequestError, Sidecar, TaskHeld

MAX_BODY = 256 * 1024


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


async def _json(request: Request) -> dict:
    body = await request.body()
    if len(body) > MAX_BODY:
        raise RequestError("request body too large")
    try:
        data = json.loads(body or b"{}")
    except ValueError as e:
        raise RequestError("the body must be JSON") from e
    if not isinstance(data, dict):
        raise RequestError("the body must be a JSON object")
    return data


def create_app(sidecar: Sidecar, host_secret: str) -> Starlette:
    if len(host_secret) < 32:
        raise ValueError("INTENTPLANE_SIDECAR_SECRET must be at least 32 characters")

    def host_only(request: Request) -> bool:
        given = _bearer(request) or ""
        return hmac.compare_digest(given.encode(), host_secret.encode())

    def refused() -> JSONResponse:
        return JSONResponse({"error": "host credential required"}, status_code=401)

    async def health(request: Request):
        return JSONResponse({"ok": True, "version": __version__, "repo": sidecar.cfg.repo,
                             "actions": sidecar.catalog()})

    async def issue(request: Request):
        if not host_only(request):
            return refused()
        try:
            d = await _json(request)
            unknown = set(d) - {"task", "strength", "short_name", "threads", "rules", "requester"}
            if unknown:
                raise RequestError(f"unexpected fields {sorted(unknown)}")
            issued = await run_in_threadpool(sidecar.issue, d.get("task"), d.get("strength"), d.get("short_name"),
                                             d.get("threads", []), d.get("rules", []), d.get("requester"))
        except RequestError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except TaskHeld as e:
            return JSONResponse({"error": str(e), "held": True}, status_code=409)
        return JSONResponse({"token": issued.token, "branch": issued.branch, "scopes": sorted(issued.scopes),
                             "expires": issued.expires}, status_code=201)

    async def withdraw(request: Request):
        if not host_only(request):
            return refused()
        try:
            d = await _json(request)
        except RequestError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        hold = d.get("hold", False)
        if not isinstance(hold, bool):
            return JSONResponse({"error": "hold must be true or false"}, status_code=400)
        done = await run_in_threadpool(sidecar.withdraw, request.path_params["task"], str(d.get("reason", "")), hold)
        return JSONResponse({"withdrawn": done, "held": sidecar.held(request.path_params["task"])},
                            status_code=200 if done else 404)

    async def release(request: Request):
        if not host_only(request):
            return refused()
        done = await run_in_threadpool(sidecar.release, request.path_params["task"])
        return JSONResponse({"released": done}, status_code=200 if done else 404)

    async def verify(request: Request):
        if not host_only(request):
            return refused()
        return JSONResponse({"ok": sidecar.ledger.verify(), "entries": len(sidecar.ledger)})

    async def screen(request: Request):
        if not host_only(request):
            return refused()
        try:
            d = await _json(request)
            unknown = set(d) - {"task", "source", "conversation", "kind", "text"}
            if unknown:
                raise RequestError(f"unexpected fields {sorted(unknown)}")
            out = await run_in_threadpool(sidecar.screen_message, d.get("task"), d.get("source"),
                                          d.get("conversation"), d.get("kind"), d.get("text"))
        except RequestError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(out)

    async def propose(request: Request):
        token = _bearer(request)
        if not token:
            return JSONResponse({"status": "denied", "stage": "authn", "message": "a mandate is required"},
                                status_code=401)
        try:
            d = await _json(request)
        except RequestError as e:
            return JSONResponse({"status": "denied", "stage": "contract", "message": str(e)}, status_code=400)
        out = await run_in_threadpool(sidecar.propose, token, d.get("action"), d.get("args", {}))
        # A refusal is an answer, not a server error: 200 with status "denied", so the agent reads the reason.
        return JSONResponse(json.loads(json.dumps(out, default=str)))

    @asynccontextmanager
    async def lifespan(app):
        if sidecar.channel is not None:
            await sidecar.channel.start()
        try:
            yield
        finally:
            if sidecar.channel is not None:
                await sidecar.channel.stop()

    return Starlette(routes=[
        Route("/v1/health", health, methods=["GET"]),
        Route("/v1/mandates", issue, methods=["POST"]),
        Route("/v1/mandates/{task}", withdraw, methods=["DELETE"]),
        Route("/v1/mandates/{task}/release", release, methods=["POST"]),
        Route("/v1/receipts/verify", verify, methods=["GET"]),
        Route("/v1/propose", propose, methods=["POST"]),
        Route("/v1/messages/screen", screen, methods=["POST"]),
    ], lifespan=lifespan)
