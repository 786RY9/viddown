"""FastAPI application: routes, lifespan, error mapping."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StringConstraints

from . import __version__
from ._core import parse_time, to_seconds
from .config import Settings
from .extract import ExtractError, Extractor, ProbeResult, Runner
from .manifest import JsonFile, build_manifest, cloud_status
from .notify import ntfy
from .resources import (
    AdmissionController,
    EgressMeter,
    KeyedConcurrency,
    Overloaded,
    PressureGuard,
    RateLimited,
    RateLimiter,
    SystemProbe,
    Ticket,
    budget_from_probe,
    periodic,
    seconds_until_next_month,
)
from .security import (
    BadToken,
    Resolver,
    StreamGrant,
    TokenSigner,
    UnsafeUrl,
    check_url,
    client_ip,
    default_resolver,
)
from .store import Store
from .streaming import (
    Pipeline,
    PipelineResponse,
    PlanError,
    build_plan,
    cleanup_stale_jobs,
    estimate_bytes,
)

log = logging.getLogger("viddown")

ERROR_STATUS = {
    "unsupported": 422,
    "unavailable": 422,
    "blocked": 502,
    "network": 504,
    "unknown": 502,
}


class ApiError(Exception):
    def __init__(self, status: int, kind: str, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.kind, self.message, self.headers = status, kind, message, headers


def error_body(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"kind": kind, "message": message, **extra}}


# ═════════════════════════════════════════════════════════════════════════════
# Request models
# ═════════════════════════════════════════════════════════════════════════════


class ClipIn(BaseModel):
    start: Annotated[str, StringConstraints(max_length=16)]
    end: Annotated[str, StringConstraints(max_length=16)]
    precise: bool = False


class ProbeIn(BaseModel):
    url: Annotated[str, StringConstraints(min_length=8, max_length=2048)]
    kind: Literal["video", "audio"] = "video"
    formatId: Annotated[str, StringConstraints(max_length=64, pattern=r"^[\w.\-]+$")] | None = None
    clip: ClipIn | None = None


class ContactIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    email: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    ]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
    source: Annotated[str, StringConstraints(max_length=40)] | None = None
    # Honeypot: humans never see this field; bots fill it.
    website: Annotated[str, StringConstraints(max_length=200)] = ""


PING_EVENTS = {"install", "launch", "download", "cloud_download"}
PING_PLATFORMS = {"android", "windows", "macos", "linux", "web"}
_VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[0-9A-Za-z.]{1,16})?$")


class PingIn(BaseModel):
    event: Annotated[str, StringConstraints(max_length=32)]
    platform: Annotated[str, StringConstraints(max_length=16)]
    appVersion: Annotated[str, StringConstraints(max_length=32)] = "unknown"


# ═════════════════════════════════════════════════════════════════════════════
# Services container
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class Services:
    s: Settings
    admission: AdmissionController
    extractor: Extractor
    signer: TokenSigner
    store: Store
    egress: EgressMeter
    guard: PressureGuard
    probe_limiter: RateLimiter
    stream_limiter: RateLimiter
    contact_limiter: RateLimiter
    ping_limiter: RateLimiter
    streams_per_ip: KeyedConcurrency
    manifest_base: JsonFile
    validated: JsonFile
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    stats_cache: tuple[float, dict[str, Any]] | None = None

    def cloud(self) -> tuple[bool, str | None]:
        return cloud_status(self.s, self.manifest_base.get(), self.egress.exceeded)


def build_services(s: Settings, probe: SystemProbe, runner: Runner | None = None) -> Services:
    budget = budget_from_probe(probe, s)
    log.info("budget: %s", budget.describe())
    admission = AdmissionController(budget, s)
    store = Store(s.state_dir / "viddown.db")
    return Services(
        s=s,
        admission=admission,
        extractor=Extractor(s, admission, runner),
        signer=TokenSigner(s.secret),
        store=store,
        egress=EgressMeter(s.egress_cutoff_bytes, store.load_egress, store.save_egress),
        guard=PressureGuard(probe, admission, s),
        probe_limiter=RateLimiter.per_minute(s.probe_rate_per_min, s.probe_burst),
        stream_limiter=RateLimiter.per_minute(s.stream_rate_per_min),
        contact_limiter=RateLimiter.per_hour(s.contact_rate_per_hour),
        ping_limiter=RateLimiter.per_hour(s.ping_rate_per_hour),
        streams_per_ip=KeyedConcurrency(s.max_streams_per_ip),
        manifest_base=JsonFile(s.manifest_base_path),
        validated=JsonFile(s.state_dir / "validated.json"),
    )


# ═════════════════════════════════════════════════════════════════════════════
# App factory
# ═════════════════════════════════════════════════════════════════════════════


def create_app(
    settings: Settings | None = None,
    *,
    system_probe: SystemProbe | None = None,
    runner: Runner | None = None,
    resolver: Resolver = default_resolver,
) -> FastAPI:
    """Build the app. ``system_probe``, ``runner`` and ``resolver`` are test seams."""
    s = settings or Settings.from_env()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if s.unsafe_allow_generic or s.unsafe_allow_private_urls:
            log.warning(
                "UNSAFE test settings enabled (generic=%s private=%s) - never use in production",
                s.unsafe_allow_generic,
                s.unsafe_allow_private_urls,
            )
        removed = cleanup_stale_jobs(s.runtime_dir)
        if removed:
            log.warning("removed %d stale job dirs from %s", removed, s.runtime_dir)
        svc = build_services(s, system_probe or SystemProbe(), runner)
        app.state.services = svc
        await svc.extractor.start()
        svc.tasks = [
            asyncio.create_task(svc.guard.run(), name="pressure-guard"),
            asyncio.create_task(periodic(s.egress_flush_s, svc.egress.flush), name="egress-flush"),
        ]
        try:
            yield
        finally:
            svc.admission.start_drain()
            for t in svc.tasks:
                t.cancel()
            for p in list(Pipeline.live):
                await p.close()
            svc.egress.flush()
            await svc.extractor.shutdown()
            svc.store.close()

    app = FastAPI(
        title="viddown server",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # The site and the desktop webview are other origins; without this the
    # browser's preflight gets a 405 and the contact form / stats / cloud
    # fallback fail. Credentials are never used, so no cookies cross origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(s.cors_origins),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
        allow_credentials=False,
        max_age=86400,
    )

    def svc_of(request: Request) -> Services:
        return request.app.state.services  # type: ignore[no-any-return]

    def ip_of(request: Request) -> str:
        peer = request.client.host if request.client else None
        return client_ip(peer, request.headers.get("x-forwarded-for"), s.trusted_proxies)

    # -- error mapping -------------------------------------------------------
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError) -> JSONResponse:
        return JSONResponse(error_body(e.kind, e.message), e.status, headers=e.headers)

    @app.exception_handler(Overloaded)
    async def _overloaded(_: Request, e: Overloaded) -> JSONResponse:
        return JSONResponse(
            error_body("busy", "Server is busy; use the local engine or retry later", reason=e.reason),
            503,
            headers={"Retry-After": str(e.retry_after)},
        )

    @app.exception_handler(RateLimited)
    async def _limited(_: Request, e: RateLimited) -> JSONResponse:
        return JSONResponse(
            error_body("rate_limited", "Too many requests", reason=e.reason),
            429,
            headers={"Retry-After": str(e.retry_after)},
        )

    @app.exception_handler(ExtractError)
    async def _extract(_: Request, e: ExtractError) -> JSONResponse:
        return JSONResponse(error_body(e.kind, e.message), ERROR_STATUS.get(e.kind, 502))

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, e: RequestValidationError) -> JSONResponse:
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", ()) if x != "body")
        return JSONResponse(error_body("invalid", f"{loc}: {first.get('msg', 'invalid request')}"), 400)

    def require_cloud(svc: Services) -> None:
        enabled, reason = svc.cloud()
        if not enabled:
            retry = seconds_until_next_month() if reason == "egress_budget" else 3600
            raise ApiError(503, "cloud_disabled", f"Cloud mode is off ({reason})", {"Retry-After": str(retry)})

    # -- routes --------------------------------------------------------------
    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        svc = svc_of(request)
        body: dict[str, Any] = {
            "status": "draining" if svc.admission.draining else "ok",
            "version": __version__,
        }
        # Details only for local callers (smoke tests, the operator over SSH).
        peer = request.client.host if request.client else ""
        if peer in ("127.0.0.1", "::1") and "x-forwarded-for" not in request.headers:
            body.update(
                budget=svc.admission.budget.__dict__,
                admission=svc.admission.snapshot(),
                memory=svc.guard.last,
                egress=svc.egress.snapshot(),
                extractor=svc.extractor.snapshot(),
                pipelines=len(Pipeline.live),
            )
        return JSONResponse(body, 503 if svc.admission.draining else 200)

    @app.get("/v1/manifest")
    async def manifest(request: Request) -> JSONResponse:
        svc = svc_of(request)
        body = build_manifest(s, svc.manifest_base.get(), svc.validated.get(), svc.egress.exceeded)
        return JSONResponse(body, headers={"Cache-Control": "public, max-age=300"})

    @app.post("/v1/probe")
    async def probe(request: Request, body: ProbeIn) -> JSONResponse:
        svc = svc_of(request)
        ip = ip_of(request)
        require_cloud(svc)
        svc.probe_limiter.check(ip, "probe_rate")
        try:
            url = await check_url(body.url, resolver, allow_private=s.unsafe_allow_private_urls)
        except UnsafeUrl as e:
            raise ApiError(400, "unsupported", str(e)) from None
        clip = _parse_clip(body.clip)
        result = await svc.extractor.probe(url)
        return JSONResponse(_probe_response(svc, result, url, body, clip, ip))

    @app.get("/v1/stream/{token}")
    async def stream(request: Request, token: str) -> Response:
        svc = svc_of(request)
        ip = ip_of(request)
        try:
            grant = svc.signer.verify(token, ip)
        except BadToken as e:
            status = 410 if e.reason == "expired" else 403
            raise ApiError(status, "bad_token", f"Stream link is invalid ({e.reason}); probe again") from None
        require_cloud(svc)
        if not svc.streams_per_ip.try_acquire(ip):
            raise RateLimited("streams_per_client", 30)
        ticket: Ticket | None = None
        pipe: Pipeline | None = None
        handed_off = False
        try:
            svc.stream_limiter.check(ip, "stream_rate")
            result = await svc.extractor.probe(grant.url)  # cache hit in the normal flow
            try:
                plan = build_plan(grant, result.info, s)
            except PlanError as e:
                raise ApiError(422, "unavailable", str(e)) from None
            ticket = await svc.admission.acquire(plan.job_class)
            pipe = Pipeline(plan, result.info_json(), s)
            try:
                await pipe.start()
            except RuntimeError as e:
                raise ApiError(501, "unsupported", str(e)) from None
            ticket.on_cancel(pipe.kill)
            # Wait for the first bytes so early failures (403, removed video)
            # become a proper JSON error instead of a 200 with an empty body.
            try:
                first = await asyncio.wait_for(pipe.read(s.chunk_size), s.stall_timeout_s)
            except TimeoutError:
                first = b""
            if not first:
                await pipe.settle()
                kind, message = pipe.error()
                if ticket.cancelled:
                    raise Overloaded(ticket.cancelled, 30)
                raise ApiError(ERROR_STATUS.get(kind, 502), kind, message)

            t, p = ticket, pipe

            async def on_close(outcome: str) -> None:
                await p.close()
                svc.admission.release(t)
                svc.streams_per_ip.release(ip)

            handed_off = True
            return PipelineResponse(pipe, first, s, svc.egress.add, on_close)
        finally:
            if not handed_off:
                if pipe is not None:
                    await pipe.close()
                if ticket is not None:
                    svc.admission.release(ticket)
                svc.streams_per_ip.release(ip)

    @app.post("/v1/contact", status_code=202)
    async def contact(request: Request, body: ContactIn) -> JSONResponse:
        svc = svc_of(request)
        svc.contact_limiter.check(ip_of(request), "contact_rate")
        if body.website:  # honeypot tripped: pretend success, store nothing
            return JSONResponse({"ok": True}, 202)
        await svc.store.aadd_contact(body.name, body.email, body.message, body.source)
        task = asyncio.create_task(
            ntfy(
                s.ntfy_url,
                s.ntfy_topic,
                f"viddown: message from {body.name}",
                f"{body.name} <{body.email}>\n\n{body.message}",
            )
        )
        svc.tasks.append(task)
        task.add_done_callback(lambda t: svc.tasks.remove(t) if t in svc.tasks else None)
        return JSONResponse({"ok": True}, 202)

    @app.post("/v1/ping", status_code=204)
    async def ping(request: Request, body: PingIn) -> Response:
        svc = svc_of(request)
        # The IP is used only as an in-memory rate-limit key and never stored.
        svc.ping_limiter.check(ip_of(request), "ping_rate")
        if body.event not in PING_EVENTS or body.platform not in PING_PLATFORMS:
            raise ApiError(400, "invalid", "unknown event or platform")
        version = body.appVersion if _VERSION_RE.match(body.appVersion) else "other"
        await svc.store.abump(body.event, body.platform, version)
        return Response(status_code=204)

    @app.get("/v1/stats")
    async def stats(request: Request) -> JSONResponse:
        svc = svc_of(request)
        now = time.monotonic()
        if svc.stats_cache is None or now - svc.stats_cache[0] > 60:
            svc.stats_cache = (now, await svc.store.astats())
        return JSONResponse(svc.stats_cache[1], headers={"Cache-Control": "public, max-age=60"})

    return app


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════


def _parse_clip(clip: ClipIn | None) -> tuple[int, int, bool] | None:
    if clip is None:
        return None
    start, end = parse_time(clip.start), parse_time(clip.end)
    if start is None or end is None:
        raise ApiError(400, "invalid", "clip times must look like 1:23, 1h2m3s or seconds")
    a, b = to_seconds(start), to_seconds(end)
    if b <= a:
        raise ApiError(400, "invalid", "clip end must be after start")
    return a, b, clip.precise


def _probe_response(
    svc: Services,
    result: ProbeResult,
    url: str,
    body: ProbeIn,
    clip: tuple[int, int, bool] | None,
    ip: str,
) -> dict[str, Any]:
    s = svc.s
    info = dict(result.info)
    info["url"] = url
    duration = info.get("durationSec")
    if clip and duration and clip[0] >= duration:
        raise ApiError(400, "invalid", "clip starts after the end of the video")
    expires = int(time.time()) + s.token_ttl_s
    start: int | None = None
    end: int | None = None
    precise = False
    if clip:
        start, end, precise = clip
        if duration:
            end = min(end, int(duration) + 1)

    def token(kind: str, fid: str) -> str:
        grant = StreamGrant(url, kind, fid, start, end, precise and kind == "video", expires)
        return svc.signer.sign(grant, ip)

    # One token per offered quality (cheap HMACs), skipping ones over the size cap.
    tokens: dict[str, str] = {}
    for f in info.get("videoFormats") or []:
        est = None if clip else estimate_bytes(f, duration)
        if est is None or est <= s.max_stream_bytes:
            tokens[str(f["format_id"])] = token("video", str(f["format_id"]))
    tokens["audio"] = token("audio", "audio")

    if body.kind == "audio":
        selected = "audio"
    elif body.formatId:
        if body.formatId not in tokens:
            raise ApiError(422, "unavailable", "that quality is not available in cloud mode")
        selected = body.formatId
    else:
        selected = next((k for k in tokens if k != "audio"), "audio")

    info["stream"] = {
        "token": tokens[selected],
        "path": f"/v1/stream/{tokens[selected]}",
        "formatId": selected,
        "kind": "audio" if selected == "audio" else "video",
        "container": "mp3" if selected == "audio" else "mp4",
        "expiresAt": expires,
    }
    info["streamTokens"] = tokens
    return info
