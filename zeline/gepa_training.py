"""Opt-in offline GEPA training search; no held-out or automatic promotion.

Callbacks are operator-trusted. The host tracks exact case/proposal budgets even
when GEPA's cooperative stopper overshoots. This is not a wall-time sandbox.
"""
from __future__ import annotations

import json
import tempfile
import time
from typing import Any, Callable


def search_training(
    *, seed_text: str, training: list[dict[str, Any]],
    score_case: Callable[[str, dict[str, Any]], bool], proposals: list[str],
    max_metric_calls: int, max_proposals: int, timeout: float,
) -> dict[str, Any]:
    """Run a deterministic, trusted training-only search and return candidates.

    No held-out argument exists; selection scores only training fixtures. Never
    reviews, applies, publishes or changes the active skill. Proposals are
    operator-supplied text, not LLM/provider outputs.
    """
    if (not isinstance(seed_text, str) or not seed_text or
            len(seed_text.encode()) > 256 * 1024 or
            not isinstance(training, list) or not 1 <= len(training) <= 16 or
            not isinstance(proposals, list) or not 1 <= len(proposals) <= 16 or
            any(not isinstance(text, str) or not text or len(text.encode()) > 256 * 1024
                for text in proposals) or
            not callable(score_case) or
            type(max_metric_calls) is not int or not 1 <= max_metric_calls <= 128 or
            type(max_proposals) is not int or not 1 <= max_proposals <= len(proposals) or
            isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
            not 0.05 <= timeout <= 30):
        raise ValueError('Invalid bounded training search inputs')
    ids = []
    for case in training:
        if not isinstance(case, dict) or set(case) != {'id', 'input'} or not isinstance(case['id'], str):
            raise ValueError('Training cases require only id and input')
        ids.append(case['id'])
    if len(set(ids)) != len(ids) or any(not case_id for case_id in ids):
        raise ValueError('Training IDs must be unique and nonempty')
    try:
        encoded = json.dumps(training, allow_nan=False, sort_keys=True).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError('Training inputs must be finite JSON') from exc
    if len(encoded) > 256 * 1024:
        raise ValueError('Training data exceeds byte budget')

    # Optional dependency: importing this module itself never loads GEPA.
    import gepa

    start = time.monotonic()
    calls = 0
    issued = 0
    violated: str | None = None

    def check(next_calls: int = 0) -> None:
        nonlocal violated
        if calls + next_calls > max_metric_calls:
            violated = 'metric call budget exceeded'
            raise RuntimeError(violated)
        if time.monotonic() - start >= timeout:
            violated = 'training search deadline exceeded'
            raise TimeoutError(violated)

    class Adapter:
        # GEPA 0.1.4 accesses the optional attribute even with a custom proposer.
        propose_new_texts = None

        def evaluate(self, batch, candidate, capture_traces=False):
            nonlocal calls
            check(len(batch))
            scores, outputs, traces = [], [], []
            for case in batch:
                check(1)
                verdict = score_case(candidate['prompt'], {'id': case['id'], 'input': case['input']})
                calls += 1
                check()
                if type(verdict) is not bool:
                    raise ValueError('Training checker must return a boolean')
                outputs.append(verdict)
                scores.append(float(verdict))
                traces.append({'id': case['id'], 'passed': verdict})
            return gepa.EvaluationBatch(outputs=outputs, scores=scores,
                                        trajectories=traces if capture_traces else None,
                                        num_metric_calls=len(batch))

        def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
            check()
            return {component: list(eval_batch.trajectories or [])
                    for component in components_to_update}

    def propose(candidate, reflective, components):
        nonlocal issued, violated
        check()
        if issued >= max_proposals:
            violated = 'proposal budget exceeded'
            raise RuntimeError(violated)
        text = proposals[issued]
        issued += 1
        return {'prompt': text}

    with tempfile.TemporaryDirectory(prefix='zeline-gepa-training-') as run_dir:
        try:
            result = gepa.optimize(
                seed_candidate={'prompt': seed_text}, trainset=training, valset=training,
                adapter=Adapter(), custom_candidate_proposer=propose,
                max_metric_calls=max_metric_calls,
                stop_callbacks=[gepa.TimeoutStopCondition(timeout_seconds=timeout),
                                gepa.ScoreThresholdStopper(threshold=1.0)],
                run_dir=run_dir, use_merge=False, display_progress_bar=False,
                raise_on_exception=True,
            )
        except Exception:
            if violated:
                if 'deadline' in violated:
                    raise TimeoutError(violated) from None
                raise RuntimeError(violated) from None
            raise
    if violated or calls > max_metric_calls or issued > max_proposals:
        raise RuntimeError(violated or 'metric or proposal budget exceeded')
    check()
    candidates = [candidate['prompt'] for candidate in result.candidates]
    scores = result.val_aggregate_scores
    if (len(candidates) > max_proposals + 1 or len(scores) != len(candidates)
            or any(not isinstance(s, str) or len(s.encode()) > 256 * 1024 for s in candidates)
            or any(type(score) not in (float, int) or not 0 <= score <= 1 for score in scores)):
        raise ValueError('GEPA returned candidates outside the output budget')
    selected = max(range(len(scores)), key=lambda index: scores[index])
    return {'scope': 'training_only', 'report_only': True, 'generalization_proven': False,
            'candidates': candidates, 'training_scores': scores, 'selected_index': selected,
            'metric_calls': calls, 'proposals': issued}


def search_training_bounded(*, max_output_bytes: int = 65536, **kwargs: Any) -> dict[str, Any]:
    """Run trusted training search in a killable Unix process group.

    This bounds wall time and report bytes, not child RAM, detached descendants,
    provider calls, tokens, or filesystem access. Callbacks must be trusted.
    """
    import multiprocessing
    import os
    import signal

    timeout = kwargs.get('timeout')
    if (isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or
            not 0.05 <= timeout <= 30 or type(max_output_bytes) is not int or
            not 32 <= max_output_bytes <= 65536):
        raise ValueError('Invalid bounded worker limits')
    try:
        context = multiprocessing.get_context('fork')
    except ValueError as exc:
        raise RuntimeError('Bounded worker requires Unix fork support') from exc
    receive, send = context.Pipe(duplex=False)

    def worker():
        receive.close()
        try:
            os.setsid()
            with open(os.devnull, 'w', encoding='utf-8') as sink:
                os.dup2(sink.fileno(), 1)
                os.dup2(sink.fileno(), 2)
                import contextlib
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    try:
                        report = search_training(**kwargs)
                        payload = json.dumps(report, allow_nan=False).encode()
                        if len(payload) > max_output_bytes:
                            payload = b'{"error":"output budget exceeded"}'
                    except TimeoutError:
                        payload = b'{"error":"training search deadline exceeded"}'
                    except ValueError:
                        payload = b'{"error":"invalid training or checker result"}'
                    except Exception:
                        payload = b'{"error":"metric or proposal budget exceeded or worker failed"}'
                    send.send_bytes(payload)
        finally:
            # End ordinary callback descendants on *every* exit, not just timeout.
            # The report is already in the pipe; SIGKILL also ends this leader.
            if os.getpgrp() == os.getpid():
                os.killpg(os.getpgrp(), signal.SIGKILL)
            send.close()

    process = context.Process(target=worker)
    process.start()
    send.close()
    started = time.monotonic()
    try:
        if not receive.poll(max(0.0, timeout - (time.monotonic() - started))):
            raise TimeoutError('bounded training worker timed out')
        try:
            payload = receive.recv_bytes(maxlength=max(256, max_output_bytes))
        except (EOFError, OSError) as exc:
            raise RuntimeError('bounded training worker exited without a valid report') from exc
        process.join(timeout=0.25)
        if process.is_alive():
            raise RuntimeError('bounded training worker did not exit')
        if process.exitcode != -signal.SIGKILL:
            raise RuntimeError('bounded training worker crashed or skipped group cleanup')
        envelope = json.loads(payload)
        if 'error' in envelope:
            if 'deadline' in envelope['error']:
                raise TimeoutError(envelope['error'])
            if 'output budget' in envelope['error']:
                raise ValueError(envelope['error'])
            raise RuntimeError(envelope['error'])
        if len(payload) > max_output_bytes or not envelope.get('report_only'):
            raise ValueError('output budget or report contract violated')
        return envelope
    finally:
        if process.is_alive():
            try:
                if os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.join(timeout=1)
        receive.close()
        process.close()
