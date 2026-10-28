"""Source contracts for the Telegram formatting already deployed in Zeline."""

from zeline.gateways import telegram


def test_api_request_progress_matches_live_provider_link():
    result = telegram._tool_progress_text("http_request", {"url": "https://api.github.com/repos/example?per_page=1"})
    assert result == (
        '⚡ <a href="https://api.github.com/repos/example?per_page=1">'
        'Sending API request-Github</a>'
    )


def test_api_request_progress_rejects_non_web_or_embedded_credentials():
    for url in ("javascript:alert(1)", "https://user:password@example.com/v1", ""):
        assert telegram._tool_progress_text("http_request", {"url": url}) == "⚡ Sending API request…"


def test_api_request_progress_html_escapes_url_attribute():
    result = telegram._tool_progress_text("http_request", {"url": 'https://api.github.com/a?x="&y=2'})
    assert 'href="https://api.github.com/a?x=&quot;&amp;y=2"' in result
    assert 'Sending API request-Github</a>' in result


def test_reflection_splitter_matches_live_three_channels():
    summary = "📙 Self-enhancement review: Proposal saved.\n\n📘 Self-improvement review: Patch applied.\n\n📗 Self-refinement review: Memory saved."
    assert list(telegram._learning_report_parts((), summary)) == [
        "📙 Self-enhancement review: Proposal saved.",
        "📘 Self-improvement review: Patch applied.",
        "📗 Self-refinement review: Memory saved.",
    ]
    legacy = "📙 Self-enhancement review: Proposal saved.\n\n💾 Plain report."
    assert list(telegram._learning_report_parts((), legacy)) == [legacy]
