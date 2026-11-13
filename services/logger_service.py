"""Logging setup + detailed call tracing for the whole source.

Two detail levels when the app runs:
  * logs/redem.log  - always DEBUG: every instrumented call with its
    arguments, result, duration and full traceback on failure.
  * console (run.bat) - follows LOG_LEVEL from .env (INFO by default).
    Set LOG_LEVEL=DEBUG to see the same detail live on the console.

The tracer (instrument_services) is applied once at startup; it wraps the
module-level functions and class methods of every service module, so no
individual file has to carry its own entry/exit logging.
"""
import asyncio
import enum
import functools
import importlib
import logging
import logging.handlers
import sys
import time
import types
from pathlib import Path
from typing import Any

from config import settings

# Parameter / dict keys whose values are masked in every log line.
SENSITIVE_KEYS = {
    "password", "passwd", "pwd", "secret", "totp", "totp_secret",
    "token", "auth", "authorization", "cookie", "proxy", "proxy_parsed",
}

MAX_VALUE_CHARS = 300    # per value
MAX_ARGS_CHARS = 400     # whole argument list per call


class LogFilter:
    @staticmethod
    def sanitize_message(message: str) -> str:
        import re
        message = re.sub(r'(password["\\\s:=]+)[^\s"\\}]+', r'\1***', message, flags=re.IGNORECASE)
        message = re.sub(r'(token["\\\s:=]+)[^\s"\\}]+', r'\1***', message, flags=re.IGNORECASE)
        message = re.sub(r'(cookie["\\\s:=]+)[^\s"\\}]+', r'\1***', message, flags=re.IGNORECASE)
        message = re.sub(r'(auth["\\\s:=]+)[^\s"\\}]+', r'\1***', message, flags=re.IGNORECASE)
        message = re.sub(r'(secret["\\\s:=]+)[^\s"\\}]+', r'\1***', message, flags=re.IGNORECASE)
        # Proxy credentials travel as "host:port:user:password" - mask the
        # password (the last segment) whenever that shape appears.
        message = re.sub(r'([a-zA-Z0-9_.\-]+:\d{2,5}:[^:\s"]+):[^:\s"]+', r'\1:***', message)
        return message


class SanitizeFilter(logging.Filter):
    """Mask secrets on every record BEFORE any handler formats it.

    A wrapper handler cannot do this: a handler delegates emitting to its
    inner stream, which then formats the record with its own (empty)
    formatter - losing the timestamp prefix. A filter attached to the real
    handler runs before that formatting, so nothing is lost.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = LogFilter.sanitize_message(str(record.msg))
        if record.args:
            # Only string arguments need sanitizing; masking ints/floats as
            # text would break %-formatting (d/f) further down the line.
            record.args = tuple(
                LogFilter.sanitize_message(arg) if isinstance(arg, str) else arg
                for arg in record.args)
        return True


class SanitizingHandler(logging.Handler):
    """Legacy wrapper kept for imports elsewhere; installs a SanitizeFilter
    on its inner handler so records keep this file's format."""
    def __init__(self, handler: logging.Handler):
        super().__init__()
        self.handler = handler

    def emit(self, record: logging.LogRecord):
        self.handler.emit(record)

    def setLevel(self, level): self.handler.setLevel(level)          # noqa: E704
    def setFormatter(self, f): self.handler.setFormatter(f)            # noqa: E704
    def addFilter(self, f): self.handler.addFilter(f)                  # noqa: E704


def setup_logging():
    log_file = settings.logging.file
    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(threadName)-12s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sanitize = SanitizeFilter()

    # The file always carries the full DEBUG trace so a run can be
    # reconstructed afterwards; the console keeps the configured level so it
    # stays readable while working.
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=settings.logging.max_bytes,
        backupCount=settings.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(sanitize)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, settings.logging.level.upper(), logging.INFO))
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sanitize)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Call tracer
# ---------------------------------------------------------------------------
_tracer = logging.getLogger("services.trace")


def _format_value(value: Any, max_chars: int = MAX_VALUE_CHARS) -> str:
    """Compact, sanitized representation of a call argument or result."""
    try:
        if isinstance(value, (bytes, bytearray)):
            text = f"<{len(value)} bytes>"
        else:
            import numpy  # opencv is a dependency; optional here
            if isinstance(value, numpy.ndarray):
                text = f"<ndarray shape={value.shape} dtype={value.dtype}>"
                value = None
            else:
                text = repr(value)
    except ImportError:
        if isinstance(value, (bytes, bytearray)):
            text = f"<{len(value)} bytes>"
        else:
            text = repr(value)
    except Exception:
        text = "<unrepr-able>"

    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                parts.append(f"{key}=***")
            else:
                parts.append(f"{key}={_format_value(item, 80)}")
        text = "{" + ", ".join(parts) + "}"

    text = LogFilter.sanitize_message(text)
    if len(text) > max_chars:
        text = text[:max_chars] + f"...({len(text)} ky tu)"
    return text


def _format_signature(fn, args, kwargs) -> str:
    """Name every argument: parameters keep their names so sensitive ones
    (password, secret, ...) are masked even when passed positionally."""
    try:
        code = fn.__code__
        names = list(code.co_varnames[:code.co_argcount])
    except AttributeError:
        names = []

    parts = []
    for i, value in enumerate(args):
        name = names[i] if i < len(names) else f"arg{i}"
        if name.lower() in SENSITIVE_KEYS:
            parts.append(f"{name}=***")
        else:
            parts.append(f"{name}={_format_value(value, 120)}")
    for key, value in kwargs.items():
        if str(key).lower() in SENSITIVE_KEYS:
            parts.append(f"{key}=***")
        else:
            parts.append(f"{key}={_format_value(value, 120)}")

    text = ", ".join(parts)
    if len(text) > MAX_ARGS_CHARS:
        text = text[:MAX_ARGS_CHARS] + f"...({len(text)} ky tu)"
    return text


def _wrap(fn):
    """Log the entry, the result (with duration) and every failure of `fn`."""
    if getattr(fn, "_redem_traced", False):
        return fn

    name = f"{fn.__module__.rsplit('.', 1)[-1]}.{fn.__qualname__}"

    if asyncio.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapper(*args, **kwargs):
            signature = _format_signature(fn, args, kwargs)
            _tracer.debug(">> %s(%s)", name, signature)
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except BaseException as e:
                _tracer.exception("XX %s(%s) LOI sau %.0fms: %s: %s",
                                  name, signature, (time.perf_counter() - start) * 1000,
                                  type(e).__name__, e)
                raise
            _tracer.debug("<< %s -> %s (%.0fms)",
                          name, _format_value(result), (time.perf_counter() - start) * 1000)
            return result
        awrapper._redem_traced = True
        return awrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        signature = _format_signature(fn, args, kwargs)
        _tracer.debug(">> %s(%s)", name, signature)
        start = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except BaseException as e:
            _tracer.exception("XX %s(%s) LOI sau %.0fms: %s: %s",
                              name, signature, (time.perf_counter() - start) * 1000,
                              type(e).__name__, e)
            raise
        _tracer.debug("<< %s -> %s (%.0fms)",
                      name, _format_value(result), (time.perf_counter() - start) * 1000)
        return result

    wrapper._redem_traced = True
    return wrapper


def _instrument_class(cls: type) -> None:
    for attr_name, attr in list(vars(cls).items()):
        # Dunders (including __init__) stay untouched; properties would lose
        # their read/write semantics when wrapped.
        if isinstance(attr, (property, types.FunctionType)) and attr_name.startswith("__") and attr_name.endswith("__"):
            continue
        if isinstance(attr, property):
            continue
        if not isinstance(attr, types.FunctionType):
            continue  # classmethods / staticmethod descriptors / class attrs
        setattr(cls, attr_name, _wrap(attr))


def instrument_module(module) -> None:
    """Wrap the module-level functions and class methods defined in `module`.

    Functions imported from other packages keep their source module, so only
    the definitions actually belonging to this module get instrumented.
    """
    if type(module) is not types.ModuleType:
        raise TypeError(f"{module!r} khong phai module")

    for attr_name, attr in list(vars(module).items()):
        if attr_name.startswith("__"):
            continue
        belongs = getattr(attr, "__module__", None) == module.__name__
        if isinstance(attr, types.ModuleType):
            continue
        if isinstance(attr, type):
            # Enums reject new attributes once defined - skip them entirely.
            if issubclass(attr, enum.Enum) or attr.__module__ != module.__name__:
                continue
            if not getattr(attr, "_redem_traced", False):
                _instrument_class(attr)
                attr._redem_traced = True
        elif isinstance(attr, types.FunctionType) and belongs:
            setattr(module, attr_name, _wrap(attr))


# Every service module worth tracing. `database` (table rows fly by too
# often), `logger_service` (it would trace itself) and `config` are omitted.
TRACE_MODULES = (
    "services.adb_service",
    "services.ldplayer_service",
    "services.installer_service",
    "services.proxy_service",
    "services.proxy_rotation",
    "services.oxyproxy_service",
    "services.bing_service",
    "services.bing_login_service",
    "services.bing_redeem_service",
    "services.rewards_service",
    "services.screen_vision",
    "services.webview_cdp",
    "services.ui_text",
    "services.ip_blacklist",
    "services.job_progress",
    "workflow",
)


def instrument_services() -> list:
    """Apply the tracer to every service module; returns what got wrapped."""
    done = []
    for name in TRACE_MODULES:
        try:
            module = importlib.import_module(name)
            instrument_module(module)
            done.append(name)
        except Exception as e:
            logging.getLogger("services.trace").warning(
                "Khong instrument duoc %s: %s: %s", name, type(e).__name__, e)
    logging.getLogger("services.trace").info(
        "Instrumented %d module(s): %s", len(done), ", ".join(done))
    return done


setup_logging()
