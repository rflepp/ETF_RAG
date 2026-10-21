"""HTTP API for the LangGraph agent. Deployed to Cloud Run (Dockerfile, .github/workflows/deploy-agent.yml).

  uvicorn agent.api:app --port 8080            # OpenAPI docs at http://localhost:8080/docs

  POST /chat       {"messages": [{"role": "user", "content": "..."}], "profile": {...}}  -> answer, sources, trace
  POST /feedback   {"run_id": "...", "score": 1, "comment": "..."}  -> stored as LangSmith feedback on that run
  GET  /healthz

Auth: bearer tokens from AGENT_API_TOKENS (comma-separated). Unset means open for local development, but on Cloud Run
(K_SERVICE is set) it fails closed so a missing secret never exposes the LLM budget.
Retrieval uses QDRANT_URL / QDRANT_API_KEY (Qdrant Cloud) when set, otherwise the embedded store in data/qdrant.
Logs are one JSON object per line, which Cloud Logging parses into structured entries.
"""
from __future__ import annotations

import hmac
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from . import llm
from .graph import Answer
from .run import make_agent, run_config, summarize, to_messages
from .sql import FundsDB
from .tools import Ctx

MAX_PROFILE_CHARS = 2000


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=12)
    profile: dict | None = Field(None, description="Answers from the guided interview (see site/js/interview.js)")

    @field_validator("messages")
    @classmethod
    def _last_from_user(cls, v: list[Message]) -> list[Message]:
        if v[-1].role != "user":
            raise ValueError("the last message must come from the user")
        return v

    @field_validator("profile")
    @classmethod
    def _small_profile(cls, v: dict | None) -> dict | None:
        if v is not None and len(json.dumps(v)) > MAX_PROFILE_CHARS:
            raise ValueError(f"profile larger than {MAX_PROFILE_CHARS} characters")
        return v


class Source(BaseModel):
    kind: str
    isin: str
    name: str | None = None
    url: str | None = None
    page: int | None = None


class ToolCall(BaseModel):
    name: str
    args: dict


class ChatResponse(BaseModel):
    run_id: str = Field(description="LangSmith run id; send it to /feedback")
    answer: Answer
    warnings: list[str]
    sources: dict[str, Source] = Field(description="Every id cited in the answer, with its document link")
    tool_calls: list[ToolCall]
    repairs: int = Field(description="Times the verify step sent the answer back to the model")
    latency_ms: int


class Feedback(BaseModel):
    run_id: uuid.UUID
    score: float = Field(ge=0, le=1)
    comment: str | None = Field(None, max_length=2000)


def _log(message: str, severity: str = "INFO", **fields) -> None:
    print(json.dumps({"severity": severity, "message": message, **fields}, default=str), flush=True)


_bearer = HTTPBearer(auto_error=False)


def _auth(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    tokens = [t.strip() for t in os.environ.get("AGENT_API_TOKENS", "").split(",") if t.strip()]
    if not tokens:
        if os.environ.get("K_SERVICE"):
            raise HTTPException(503, "the service has no API tokens configured")
        return
    if creds is None or not any(hmac.compare_digest(creds.credentials, t) for t in tokens):
        raise HTTPException(401, "unknown or missing token")


def create_app(retriever=None, model=None) -> FastAPI:
    """retriever / model: injected by tests; by default Qdrant and the LLM_CHAIN providers."""
    shared: dict = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        r = retriever
        if r is None:
            from pipeline.index_qdrant import client

            from .retrieval import Retriever
            r = Retriever(client())
        funds = r.all_funds()  # cached for the shortlist and SQL tools instead of a full scroll per request
        shared.update(retriever=r, funds=funds, db=FundsDB(funds))
        _log("ready", funds=len(funds), llm_chain=llm.chain(False))
        yield

    app = FastAPI(title="ETF Compass agent", version="0.1.0", lifespan=lifespan,
                  description="Tool-using research agent over Swiss-listed UCITS ETFs, with verified citations.")
    origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type"])

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "funds": len(shared.get("funds", [])), "llm_chain": llm.chain(False),
                "tracing": os.environ.get("LANGSMITH_TRACING", "").lower() == "true"}

    @app.post("/chat", response_model=ChatResponse, dependencies=[Depends(_auth)])
    def chat(req: ChatRequest) -> ChatResponse:
        t0 = time.perf_counter()
        profile = req.profile or {}
        ctx = Ctx(shared["retriever"], profile=profile, funds_cache=shared["funds"], db_cache=shared["db"])
        cfg = run_config(profile, tags=["api"])
        try:
            state = make_agent(ctx, model=model).invoke(
                {"messages": to_messages([m.model_dump() for m in req.messages]), "profile": profile}, config=cfg)
        except Exception as e:  # every provider in the chain failed (quota, timeout, outage)
            _log("chat failed", "ERROR", run_id=cfg["run_id"], error=repr(e))
            raise HTTPException(503, "The language model is unavailable right now. Please try again later.") from e
        out = summarize(state)
        ms = round((time.perf_counter() - t0) * 1000)
        _log("chat", run_id=cfg["run_id"], latency_ms=ms, tools=[c["name"] for c in out["tool_calls"]],
             repairs=out["repairs"], warnings=len(out["warnings"]), cited=len(out["sources"]))
        return ChatResponse(run_id=str(cfg["run_id"]), latency_ms=ms, answer=out["answer"], warnings=out["warnings"],
                            sources=out["sources"], tool_calls=out["tool_calls"], repairs=out["repairs"])

    @app.post("/feedback", status_code=202, dependencies=[Depends(_auth)])
    def feedback(fb: Feedback) -> dict:
        if not os.environ.get("LANGSMITH_API_KEY"):
            raise HTTPException(503, "feedback needs LangSmith (LANGSMITH_API_KEY)")
        from langsmith import Client
        Client().create_feedback(run_id=fb.run_id, key="user_score", score=fb.score, comment=fb.comment)
        return {"ok": True}

    return app


app = create_app()
