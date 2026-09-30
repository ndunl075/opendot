"""`opendot measure` argument parsing and dispatch."""

from __future__ import annotations

import argparse
import importlib
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ..providers.errors import ProviderError
from .fake import FakeProvider
from .plan import Aborted, MeasureContext, run_plan
from .report import render_report

DEFAULT_PROVIDER = "chatgpt_plan"
SIGN_IN_MESSAGE = (
    "Cannot run `opendot measure`: {reason}. Sign in to your ChatGPT account in OpenDot first, "
    "then run it again. (Use `opendot measure --dry-run` to try the tool without an account.)"
)


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "measure",
        help="run the scripted plan measurements (ARCHITECTURE.md 8.5) and write docs/measurements.md",
    )
    parser.add_argument("--provider", default=DEFAULT_PROVIDER, help="provider name (default: chatgpt_plan)")
    parser.add_argument("--out", help="report path (default docs/measurements.md; with --dry-run, stdout)")
    parser.add_argument("--max-calls", type=int, default=12, help="hard budget of provider calls (default 12)")
    parser.add_argument("--model", help="model id to test with (default: the catalog's luna model)")
    parser.add_argument("--allow-paid", action="store_true", help="allow a provider that charges per token")
    parser.add_argument("--provider-factory", help="module:function returning a Provider (advanced)")
    parser.add_argument("--dry-run", action="store_true", help="use a simulated provider; no account, no network")
    parser.add_argument("--fake-astra", action="store_true", help="with --dry-run, include Astra in the catalog")


def default_out_path() -> Path:
    repo = Path(__file__).resolve().parents[4]
    return (repo / "docs" / "measurements.md") if (repo / "docs").is_dir() else Path("docs") / "measurements.md"


class MeasureError(Exception):
    """A real run could not start."""


def _from_hook(spec: str) -> Any:
    module_name, _, function = spec.partition(":")
    if not module_name or not function:
        raise MeasureError("--provider-factory must look like module:function")
    try:
        return getattr(importlib.import_module(module_name), function)()
    except (ImportError, AttributeError) as error:
        raise MeasureError(f"cannot load provider factory {spec!r}: {error}") from error


def build_provider(args: argparse.Namespace) -> Any:
    if args.provider_factory:
        return _from_hook(args.provider_factory)
    try:
        factory = importlib.import_module("opendot_core.providers.factory")
    except ImportError:
        factory = None
    if factory is not None and hasattr(factory, "build_registry"):
        registry = factory.build_registry()
        try:
            return registry.get(args.provider)
        except ProviderError as error:
            raise MeasureError(error.user_message) from error
    if args.provider == DEFAULT_PROVIDER:
        try:
            module = importlib.import_module("opendot_core.providers.chatgpt_plan")
            return module.ChatGPTPlanProvider()
        except (ImportError, AttributeError) as error:
            raise MeasureError("the ChatGPT plan provider is not available in this build") from error
        except ProviderError as error:
            raise MeasureError(error.user_message) from error
    raise MeasureError(f"unknown provider {args.provider!r}; pass --provider-factory module:function")


def run(
    args: argparse.Namespace,
    *,
    clock: Callable[[], date] = date.today,
    out: Any = None,
    err: Any = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    if args.max_calls < 1:
        print("--max-calls must be at least 1", file=err)
        return 2
    if args.dry_run:
        provider: Any = FakeProvider(astra=args.fake_astra)
    else:
        try:
            provider = build_provider(args)
        except MeasureError as error:
            print(SIGN_IN_MESSAGE.format(reason=error), file=err)
            return 2
        except Exception as error:
            print(SIGN_IN_MESSAGE.format(reason=f"provider setup failed ({type(error).__name__})"), file=err)
            return 2
    if getattr(provider, "paid", False) and provider.name != DEFAULT_PROVIDER and not args.allow_paid:
        print(
            f"Refusing to measure {provider.name!r}: it charges per token. Pass --allow-paid to accept that.",
            file=err,
        )
        return 2
    ctx = MeasureContext(provider=provider, max_calls=args.max_calls, model=args.model)
    try:
        results = run_plan(ctx)
    except Aborted as aborted:
        print(SIGN_IN_MESSAGE.format(reason=aborted.error.user_message), file=err)
        return 2
    report = render_report(
        results,
        provider=provider.name,
        today=clock(),
        calls_used=ctx.calls,
        max_calls=ctx.max_calls,
        dry_run=args.dry_run,
    )
    if args.dry_run and not args.out:
        out.write(report)
    else:
        path = Path(args.out) if args.out else default_out_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8", newline="\n")
        print(f"wrote {path}", file=out)
    for result in results:
        if result.warning:
            print(f"WARNING: {result.warning}", file=err)
    return 0
