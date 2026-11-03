"""Recognize explicit branching questions omitted from ask_user tool calls.

Only final decision questions with contiguous choices qualify. Informational lists
and incidental questions must remain regular replies.
"""
from __future__ import annotations

import re

_OPTION = re.compile(r"^([1-6A-Fa-f])[.):]\s+(.+?)\s*$")
_BULLET = re.compile(r"^[-*•]\s+(.+?)\s*$")
_DECISION = re.compile(r"\b(?:pilih|pilihan|opsi|mana|mau|choose|which|prefer)\b", re.IGNORECASE)
_TIMING_DECISION = re.compile(
    r'^(?:kapan\s+(?:gue|aku|saya|kami|kita)\s+(?:pasang|deploy|restart|jalankan|terapkan|mulai)\b|'
    r'when\s+(?:should|shall|may|can)\s+(?:i|we)\b)', re.IGNORECASE)
_FREEFORM = re.compile(r"^(?:atau|or)\s+(?:ketik|tulis|type|write)\b", re.IGNORECASE)
_BINARY = re.compile(
    r"^(?:Pilih|Mau|Choose|Would you prefer)\s+(.{1,150}?)\s+(?:atau|or)\s+(.{1,150}?)\?$",
    re.IGNORECASE,
)


def _choice(line: str) -> tuple[str | None, str] | None:
    """Read numbered, lettered, or bulleted option; remove markup only."""
    item = line.strip().replace("**", "").replace("`", "")
    bullet = _BULLET.fullmatch(item)
    if bullet:
        item = bullet.group(1)
    labelled = _OPTION.fullmatch(item)
    if labelled:
        return labelled.group(1).upper(), labelled.group(2).strip()
    if bullet:
        return None, item.strip()
    return None


def _choices(lines: list[str], question: str) -> tuple[str, ...] | None:
    decision = bool(_DECISION.search(question) or _TIMING_DECISION.search(question))
    if not 2 <= len(lines) <= 6 or not decision:
        # Enumerating information is not an explicit request for a decision.
        # Numbered/lettered options need the same intent cue as bullet options.
        return None
    found = [_choice(item) for item in lines]
    if any(item is None or not item[1] for item in found):
        return None
    labels = [item[0] for item in found if item is not None]
    if any(label is None for label in labels):
        if any(label is not None for label in labels) or not decision:
            return None
    elif labels != [str(i) for i in range(1, len(lines) + 1)] and labels != [chr(65 + i) for i in range(len(lines))]:
        return None
    options = tuple(item[1] for item in found if item is not None)
    return options if len({option.casefold() for option in options}) == len(options) else None


def confirmation_choices(question: str, options: tuple[str, ...]) -> tuple[tuple[str, str], ...] | None:
    """Style only clear approval/rejection pairs; ordinary options keep letter buttons."""
    if len(options) != 2 or any(len(option) > 48 for option in options):
        return None
    normalized = [option.strip().casefold().rstrip('.!') for option in options]
    positive = {'yes', 'y', 'ya', 'iya', 'ok', 'okay', 'setuju', 'agree', 'confirm',
                'konfirmasi', 'proceed', 'continue', 'lanjut', 'lanjutkan', 'approve', 'allow', 'izinkan'}
    positive.update({'save', 'simpan', 'apply', 'terapkan', 'enable', 'aktifkan',
                     'disable', 'nonaktifkan', 'restart', 'deploy', 'delete', 'hapus',
                     'remove', 'run', 'retry', 'ulangi', 'install', 'pasang', 'update', 'perbarui'})
    negative = {'no', 'n', 'tidak', 'nggak', 'enggak', 'cancel', 'batal', 'batalkan',
                'not now', 'not yet', 'nanti', 'nanti aja', 'tunda', 'reject', 'decline',
                'skip', 'jangan', 'jangan dulu', 'nggak dulu', 'no thanks', 'no, thanks', 'disagree'}
    transaction_cta = re.compile(
        r'(?:(?:yes|ya|iya)\s*,?\s+)?(?:'
        r'(?:mint|list|sell|buy|swap|stake|unstake|withdraw|deposit|transfer|send|pay|claim|jual|beli|bayar|kirim)'
        r'(?: (?:now|sekarang))?|confirm (?:transaction|payment)|konfirmasi (?:transaksi|pembayaran))'
    )
    natural_cta = re.compile(
        r'(?:(?:yes|ya|iya)\s*,?\s+)?(?:save|simpan|apply|terapkan|enable|aktifkan|disable|'
        r'nonaktifkan|restart|deploy|delete|hapus|remove|run|retry|ulangi|install|pasang|update|perbarui)'
        r'(?: (?:changes|perubahan|settings|pengaturan|gateway|service|it|ini))?'
    )
    def contextual_action(label: str) -> str | None:
        # Accept short action + subject labels only when that subject is in the question.
        # Do not guess mismatched assets or reduce conditional consent to a simple approval.
        match = re.fullmatch(
            r'(mint|list|sell|buy|swap|stake|unstake|withdraw|deposit|transfer|send|pay|claim|'
            r'save|apply|enable|disable|restart|deploy|delete|remove|run|retry|install|update)'
            r'\s+(.+?)(?:\s+now)?', label)
        if not match or re.search(r"\b(?:if|unless|but|except|not|no|without|don't|jangan|tidak)\b", label):
            return None
        context = ' '.join(question.casefold().split())
        subject = ' '.join(match.group(2).split())
        if not re.search(r'(?<!\w)' + re.escape(subject) + r'(?!\w)', context):
            return None
        return match.group(1)

    roles = [1 if item in positive or transaction_cta.fullmatch(item) or natural_cta.fullmatch(item)
             or contextual_action(item) else -1 if item in negative else 0 for item in normalized]
    if sorted(roles) != [-1, 1]:
        return None
    approval = normalized[roles.index(1)]
    financial_domain = re.search(
        r'\b(?:nft|tokens?|crypto|kripto|wallet|uang|rupiah|usdt|usdc|eth|btc|sol|bnb|'
        r'transaksi|transactions?|payment|invoice|pembayaran|blockchain)\b|gas fee', question, re.I)
    financial_action = re.search(
        r'\b(?:mint|list|sell|buy|swap|stake|unstake|withdraw|deposit|transfer|send|pay|claim|'
        r'confirm|approve|proceed|lanjut|lanjutkan|konfirmasi|kirim|jual|beli|bayar)\b',
        question + ' ' + approval, re.I)
    inherent_money_cta = re.fullmatch(
        r'(?:(?:yes|ya|iya)\s*,?\s+)?(?:mint|sell|buy|stake|unstake|withdraw|deposit|pay|claim|'
        r'jual|beli|bayar)(?: (?:now|sekarang))?|confirm (?:transaction|payment)|'
        r'konfirmasi (?:transaksi|pembayaran)', approval)
    contextual_money = contextual_action(approval) in {
        'mint', 'sell', 'buy', 'stake', 'unstake', 'withdraw', 'deposit', 'pay', 'claim'}
    financial = bool(inherent_money_cta or contextual_money or (financial_domain and financial_action))
    return tuple((option, 'danger' if role == -1 else 'success' if financial else 'primary')
                 for option, role in zip(options, roles))


def branching_question(content: str) -> tuple[str, tuple[str, ...]] | None:
    """Return (question, options) only for an explicit final decision request."""
    text = str(content or "").strip()
    if not text or len(text) > 6_000:
        return None
    # Preserve the full bounded transaction summary; never truncate consent context to fit a card.
    yes_no = re.fullmatch(r'([\s\S]+\?)\s*\(\s*(?:y\s*/\s*n|yes\s*/\s*no)\s*\)', text, re.I)
    if yes_no is not None:
        question = yes_no.group(1).strip()
        return (question, ('Yes', 'No')) if len(question) <= 500 else None
    raw_lines = text.splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]
    for index, raw_line in enumerate(raw_lines):
        line = raw_line.strip().rstrip("* \t")
        if not line.endswith("?"):
            continue
        start = index
        while start > 0:
            preceding = raw_lines[start - 1].strip()
            if not preceding or preceding.endswith((".", "?", ":")) or _choice(preceding):
                break
            start -= 1
        question = " ".join(raw_lines[pos].strip() for pos in range(start, index + 1))
        question = question.replace("**", "").removeprefix("❓").strip()
        if len(question) > 500:
            continue
        following = [item.strip() for item in raw_lines[index + 1:] if item.strip()]
        if following and following[0].lower().rstrip(":") in {"pilihan", "options", "opsi"}:
            following = following[1:]
        if following and _FREEFORM.match(following[-1]):
            following = following[:-1]
        options = _choices(following, question)
        if options:
            return question, options
        # Some models enumerate first and put "Mau pilih yang mana?" last.
        if index == next((i for i in range(len(raw_lines) - 1, -1, -1) if raw_lines[i].strip()), -1) and _DECISION.search(question):
            before = [item.strip() for item in raw_lines[:index] if item.strip()]
            preceding_choices: list[str] = []
            for item in reversed(before):
                if _choice(item) is None:
                    break
                preceding_choices.append(item)
            options = _choices(list(reversed(preceding_choices)), question)
            if options:
                return question, options
    if len(lines) == 1:
        match = _BINARY.fullmatch(text)
        if match:
            binary_options = (match.group(1).strip(), match.group(2).strip())
            if len({x.casefold() for x in binary_options}) == 2:
                return text, binary_options
    return None
