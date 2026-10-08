"""External service connectors for Zeline.

A connector links the agent to an outside service (GitHub, Gmail, ...) so
native tools can act on the operator's behalf. Credentials are kept in
``~/.zeline/connectors/<id>.json`` (mode 0600) and are never logged.

Phase 1 ships the framework plus the GitHub connector; phase 2 adds Google
(Gmail, Calendar, Sheets, Drive) via the OAuth2 helpers in
:mod:`zeline.connectors.oauth`; phase 3 adds WhatsApp via the Business
Cloud API.
"""
from __future__ import annotations

from zeline.connectors.base import BaseConnector

REGISTRY: dict[str, BaseConnector] = {}


def register(conn: BaseConnector) -> BaseConnector:
    """Register a connector instance under its id."""
    REGISTRY[conn.id] = conn
    return conn


def get(cid: str) -> BaseConnector | None:
    """Return the connector for *cid*, or None when unknown."""
    return REGISTRY.get(cid)


def all_ids() -> list[str]:
    """Sorted connector ids."""
    return sorted(REGISTRY)


def all() -> list[BaseConnector]:
    """All registered connectors, sorted by id."""
    return [REGISTRY[cid] for cid in all_ids()]


# Built-in connectors self-register on import.
from zeline.connectors import github as _github  # noqa: E402,F401
from zeline.connectors import google as _google  # noqa: E402,F401
from zeline.connectors import whatsapp as _whatsapp  # noqa: E402,F401
from zeline.connectors import airtable as _airtable  # noqa: E402,F401
from zeline.connectors import asana as _asana  # noqa: E402,F401
from zeline.connectors import bitbucket as _bitbucket  # noqa: E402,F401
from zeline.connectors import bluesky as _bluesky  # noqa: E402,F401
from zeline.connectors import calendly as _calendly  # noqa: E402,F401
from zeline.connectors import clickup as _clickup  # noqa: E402,F401
from zeline.connectors import cloudflare as _cloudflare  # noqa: E402,F401
from zeline.connectors import confluence as _confluence  # noqa: E402,F401
from zeline.connectors import datadog as _datadog  # noqa: E402,F401
from zeline.connectors import devto as _devto  # noqa: E402,F401
from zeline.connectors import discord as _discord  # noqa: E402,F401
from zeline.connectors import dropbox as _dropbox  # noqa: E402,F401
from zeline.connectors import gitlab as _gitlab  # noqa: E402,F401
from zeline.connectors import hackernews as _hackernews  # noqa: E402,F401
from zeline.connectors import height as _height  # noqa: E402,F401
from zeline.connectors import hubspot as _hubspot  # noqa: E402,F401
from zeline.connectors import intercom as _intercom  # noqa: E402,F401
from zeline.connectors import jenkins as _jenkins  # noqa: E402,F401
from zeline.connectors import jira as _jira  # noqa: E402,F401
from zeline.connectors import monday as _monday  # noqa: E402,F401
from zeline.connectors import linear as _linear  # noqa: E402,F401
from zeline.connectors import mailgun as _mailgun  # noqa: E402,F401
from zeline.connectors import mastodon as _mastodon  # noqa: E402,F401
from zeline.connectors import notion as _notion  # noqa: E402,F401
from zeline.connectors import npm_registry as _npm_registry  # noqa: E402,F401
from zeline.connectors import onesignal as _onesignal  # noqa: E402,F401
from zeline.connectors import opsgenie as _opsgenie  # noqa: E402,F401
from zeline.connectors import pagerduty as _pagerduty  # noqa: E402,F401
from zeline.connectors import pushover as _pushover  # noqa: E402,F401
from zeline.connectors import pypi_registry as _pypi_registry  # noqa: E402,F401
from zeline.connectors import reddit as _reddit  # noqa: E402,F401
from zeline.connectors import render as _render  # noqa: E402,F401
from zeline.connectors import resend as _resend  # noqa: E402,F401
from zeline.connectors import rubygems as _rubygems  # noqa: E402,F401
from zeline.connectors import sendgrid as _sendgrid  # noqa: E402,F401
from zeline.connectors import sentry as _sentry  # noqa: E402,F401
from zeline.connectors import shortcut as _shortcut  # noqa: E402,F401
from zeline.connectors import teams as _teams  # noqa: E402,F401
from zeline.connectors import teamwork as _teamwork  # noqa: E402,F401
from zeline.connectors import slack as _slack  # noqa: E402,F401
from zeline.connectors import stripe as _stripe  # noqa: E402,F401
from zeline.connectors import telegram_bot as _telegram_bot  # noqa: E402,F401
from zeline.connectors import todoist as _todoist  # noqa: E402,F401
from zeline.connectors import trello as _trello  # noqa: E402,F401
from zeline.connectors import twilio as _twilio  # noqa: E402,F401
from zeline.connectors import vercel as _vercel  # noqa: E402,F401
from zeline.connectors import vonage as _vonage  # noqa: E402,F401
from zeline.connectors import wrike as _wrike  # noqa: E402,F401
from zeline.connectors import x_api as _x_api  # noqa: E402,F401
from zeline.connectors import zendesk as _zendesk  # noqa: E402,F401
from zeline.connectors import typeform as _typeform  # noqa: E402,F401
from zeline.connectors import tally as _tally  # noqa: E402,F401
from zeline.connectors import jotform as _jotform  # noqa: E402,F401
from zeline.connectors import surveymonkey as _surveymonkey  # noqa: E402,F401
from zeline.connectors import openweathermap as _openweathermap  # noqa: E402,F401
from zeline.connectors import coinbase as _coinbase  # noqa: E402,F401
from zeline.connectors import wise as _wise  # noqa: E402,F401
from zeline.connectors import paypal as _paypal  # noqa: E402,F401
from zeline.connectors import linkedin as _linkedin  # noqa: E402,F401
from zeline.connectors import producthunt as _producthunt  # noqa: E402,F401
from zeline.connectors import gitbook as _gitbook  # noqa: E402,F401
from zeline.connectors import ghost as _ghost  # noqa: E402,F401
from zeline.connectors import zoho_crm as _zoho_crm  # noqa: E402,F401
from zeline.connectors import pipedrive as _pipedrive  # noqa: E402,F401
from zeline.connectors import freshdesk as _freshdesk  # noqa: E402,F401
from zeline.connectors import close as _close  # noqa: E402,F401
from zeline.connectors import chargebee as _chargebee  # noqa: E402,F401
from zeline.connectors import paddle as _paddle  # noqa: E402,F401
from zeline.connectors import box as _box  # noqa: E402,F401
from zeline.connectors import webflow as _webflow  # noqa: E402,F401
from zeline.connectors import activecampaign as _activecampaign  # noqa: E402,F401
from zeline.connectors import algolia as _algolia  # noqa: E402,F401
from zeline.connectors import beehiiv as _beehiiv  # noqa: E402,F401
from zeline.connectors import betterstack as _betterstack  # noqa: E402,F401
from zeline.connectors import buffer as _buffer  # noqa: E402,F401
from zeline.connectors import convertkit as _convertkit  # noqa: E402,F401
from zeline.connectors import cronitor as _cronitor  # noqa: E402,F401
from zeline.connectors import digitalocean as _digitalocean  # noqa: E402,F401
from zeline.connectors import fathom as _fathom  # noqa: E402,F401
from zeline.connectors import flyio as _flyio  # noqa: E402,F401
from zeline.connectors import healthchecks as _healthchecks  # noqa: E402,F401
from zeline.connectors import heroku as _heroku  # noqa: E402,F401
from zeline.connectors import hetzner as _hetzner  # noqa: E402,F401
from zeline.connectors import mailchimp as _mailchimp  # noqa: E402,F401
from zeline.connectors import meilisearch as _meilisearch  # noqa: E402,F401
from zeline.connectors import n8n as _n8n  # noqa: E402,F401
from zeline.connectors import plausible as _plausible  # noqa: E402,F401
from zeline.connectors import railway as _railway  # noqa: E402,F401
from zeline.connectors import typesense as _typesense  # noqa: E402,F401
from zeline.connectors import vultr as _vultr  # noqa: E402,F401
from zeline.connectors import lemlist as _lemlist  # noqa: E402,F401
from zeline.connectors import apollo as _apollo  # noqa: E402,F401
from zeline.connectors import hunter as _hunter  # noqa: E402,F401
from zeline.connectors import bitly as _bitly  # noqa: E402,F401
from zeline.connectors import cloudinary as _cloudinary  # noqa: E402,F401
from zeline.connectors import bunnycdn as _bunnycdn  # noqa: E402,F401
from zeline.connectors import polar as _polar  # noqa: E402,F401
from zeline.connectors import lemon_squeezy as _lemon_squeezy  # noqa: E402,F401
from zeline.connectors import crates_io as _crates_io  # noqa: E402,F401
from zeline.connectors import packagist as _packagist  # noqa: E402,F401
