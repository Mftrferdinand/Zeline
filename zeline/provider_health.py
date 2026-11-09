"""Opt-in, payload-free observations of actual model request outcomes."""
from contextvars import ContextVar
from functools import wraps
import json

_CURRENT = ContextVar('zeline_provider_request', default=None)
CATEGORIES = frozenset({'credit_exhausted', 'rate_limited', 'overloaded', 'auth_error', 'error'})
_CODES = {
    'insufficient_quota': 'credit_exhausted', 'insufficient_credits': 'credit_exhausted',
    'credit_exhausted': 'credit_exhausted', 'credits_exhausted': 'credit_exhausted',
    'insufficient_balance': 'credit_exhausted', 'billing_hard_limit_reached': 'credit_exhausted',
    'insufficient_user_quota': 'credit_exhausted',
    'rate_limit_error': 'rate_limited', 'rate_limit_exceeded': 'rate_limited',
    'overloaded_error': 'overloaded', 'authentication_error': 'auth_error',
    'permission_error': 'auth_error', 'invalid_api_key': 'auth_error',
}


def safe_http(value):
    return value if type(value) is int and 100 <= value <= 599 else None


def category(status):
    return {402: 'credit_exhausted', 429: 'rate_limited', 529: 'overloaded',
            401: 'auth_error', 403: 'auth_error'}.get(status, 'error')


def _emit(context, status):
    try:
        context['observer']({'status': status, 'category': context['category'] if status == 'error' else None,
                             'http_status': context['http_status']})
    except Exception:
        pass


def _error_payload(response):
    """Inspect only an already buffered, <=4KiB error body; never do I/O.

    A socket inactivity timeout cannot bound a dripping stream. Optional
    telemetry runs outside the request's cancellation/deadline worker, so it
    must skip uncached bodies entirely rather than weakening that boundary.
    """
    content = getattr(response, '_content', None)
    if not isinstance(content, bytes) or len(content) > 4096:
        return None
    try:
        return json.loads(content)
    except (ValueError, UnicodeError):
        return None


def response_received(response):
    """Called for EACH retry; a response start never emits healthy."""
    context = _CURRENT.get()
    if context is None:
        return
    status = safe_http(getattr(response, 'status_code', None))
    context.update(http_status=status, category=category(status), failed=bool(status and status >= 400))
    if context['failed']:
        error_envelope(_error_payload(response))
        _emit(context, 'error')


def error_envelope(payload):
    """Inspect only exact structured error codes, never message text or bodies."""
    context = _CURRENT.get()
    if context is None or not isinstance(payload, dict):
        return
    error = payload.get('error')
    if not error:
        return
    context['failed'] = True
    if isinstance(error, dict):
        for field in ('code', 'type'):
            code = error.get(field)
            if isinstance(code, str) and code in _CODES:
                context['category'] = _CODES[code]
                break


def observed(call):
    @wraps(call)
    def wrapped(self, *args, **kwargs):
        observer = getattr(self, '_on_provider', None)
        if observer is None:
            return call(self, *args, **kwargs)
        current = _CURRENT.get()
        if current is not None and current['observer'] is observer:
            return call(self, *args, **kwargs)
        context = {'observer': observer, 'http_status': None, 'category': 'error', 'failed': False}
        token = _CURRENT.set(context)
        try:
            result = call(self, *args, **kwargs)
        except Exception as exc:
            # No actual HTTP/transport attempt means no provider evidence.
            # Cancellation/budget/local configuration must not invent failures.
            if context['http_status'] is not None or context.get('attempted'):
                if type(exc).__name__ not in {'_TurnCancelled', 'BudgetExceeded'}:
                    _emit(context, 'error')
            raise
        else:
            if context['http_status'] is not None:
                if not isinstance(result, dict) or not (result.get('content') or result.get('tool_calls')):
                    context['failed'] = True
                _emit(context, 'error' if context['failed'] else 'healthy')
            return result
        finally:
            _CURRENT.reset(token)
    return wrapped


def request_started():
    context = _CURRENT.get()
    if context is not None:
        context.update(attempted=True, http_status=None, category='error', failed=False)
