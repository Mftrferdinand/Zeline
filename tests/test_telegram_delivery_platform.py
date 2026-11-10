"""Receipt privacy setup fails closed; native ACL correctness remains a Windows gate."""
import importlib
import json
from unittest.mock import patch

import pytest

from zeline import telegram_delivery


@pytest.fixture
def session_recovery():
    # Persistence fixtures reload modules under disposable homes; patch the live module.
    return importlib.import_module('zeline.session_recovery')


def test_windows_receipt_rejects_unverified_directory(tmp_path, session_recovery):
    with patch.object(telegram_delivery.os, 'name', 'nt'), \
         patch.object(session_recovery, '_windows_private', return_value=False):
        with pytest.raises(PermissionError, match='directory'):
            telegram_delivery.FinalReceipt(tmp_path, 1, ['private'])
    assert not list((tmp_path / 'telegram-outbox').glob('*.json'))


def test_windows_receipt_rejects_unverified_file_before_writing(tmp_path, session_recovery):
    with patch.object(telegram_delivery.os, 'name', 'nt'), \
         patch.object(session_recovery, '_windows_private', side_effect=[True, False]):
        with pytest.raises(PermissionError, match='file'):
            telegram_delivery.FinalReceipt(tmp_path, 1, ['private'])
    assert not list((tmp_path / 'telegram-outbox').iterdir())


def test_windows_receipt_requests_private_directory_and_staging_file(tmp_path, session_recovery):
    with patch.object(telegram_delivery.os, 'name', 'nt'), \
         patch.object(session_recovery, '_windows_private', return_value=True) as guard:
        receipt = telegram_delivery.FinalReceipt(tmp_path, 1, ['private'])
    assert len(guard.call_args_list) == 2
    assert all(call.kwargs == {'create': True} for call in guard.call_args_list)
    assert guard.call_args_list[0].args == (receipt.directory,)
    assert json.loads(receipt.path.read_text(encoding='utf-8'))['parts'][0]['state'] == 'PENDING'
