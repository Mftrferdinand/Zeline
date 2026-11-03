"""Semantic confirmation styling must preserve the owned question/callback flow."""
import importlib
import threading
from unittest import mock
from types import SimpleNamespace

from tests.test_ask_user import AskUserBase


class ConfirmationCardTests(AskUserBase):
    def setUp(self):
        super().setUp()
        self.telegram = importlib.import_module('zeline.gateways.telegram')

    def buttons(self, question, options):
        entry = SimpleNamespace(question=question, options=tuple(options), request_id='owned-request')
        return [row[0] for row in self.telegram._ask_keyboard(1, entry)['inline_keyboard']]

    def test_contextual_english_asset_labels_preserve_words_and_two_buttons(self):
        cases = [('Mint the NFT Orbit #42?', ('Mint Orbit #42', 'Cancel'), 'success'),
                 ('Sell BTC from this wallet?', ('Sell BTC Now', 'Not now'), 'success'),
                 ('Mint the NFT Orbit #42?', ('Mint now', 'Cancel'), 'success'),
                 ('List NFT Orbit #42 for sale?', ('List Orbit #42', 'Cancel'), 'success'),
                 ('Send the report?', ('Send report', 'Cancel'), 'primary')]
        for question, options, style in cases:
            with self.subTest(options=options):
                buttons = self.buttons(question, options)
                self.assertEqual([b['text'] for b in buttons], list(options))
                self.assertEqual([b.get('style') for b in buttons], [style, 'danger'])
        for label in ['Sell ETH Now', 'Sell BTC unless price drops', 'Sell BTC but wait']:
            buttons = self.buttons('Sell BTC from this wallet?', [label, 'Cancel'])
            self.assertEqual([b['text'] for b in buttons], ['A', 'B', 'Fill in yourself'])

    def test_natural_confirmation_labels_need_no_now_suffix_or_third_button(self):
        cases = [('Simpan perubahan ini?', ('Simpan', 'Batal'), 'primary'),
                 ('Lanjutkan proses ini?', ('Iya', 'Nggak'), 'primary'),
                 ('Terapkan pengaturan ini?', ('Ya, terapkan', 'Jangan dulu'), 'primary'),
                 ('Mint NFT ini?', ('Mint', 'Nanti'), 'success'),
                 ('Pasang NFT ini untuk dijual?', ('List', 'Batal'), 'success'),
                 ('Jual token ini?', ('Jual', 'Nggak dulu'), 'success'),
                 ('Kirim token ke wallet ini?', ('Kirim', 'Batal'), 'success'),
                 ('Send this report?', ('Send', 'Cancel'), 'primary')]
        for question, options, style in cases:
            with self.subTest(question=question):
                buttons = self.buttons(question, options)
                self.assertEqual([b['text'] for b in buttons], list(options))
                self.assertEqual([b.get('style') for b in buttons], [style, 'danger'])
                self.assertTrue(all('copy_text' not in b for b in buttons))
                self.assertEqual([b['callback_data'] for b in buttons],
                                 ['ask:1:owned-request:0', 'ask:1:owned-request:1'])

    def test_visual_preview_callback_cannot_answer_or_change_model(self):
        sessions = mock.Mock()
        with mock.patch.object(self.telegram, '_api_call') as api, \
             mock.patch.object(self.telegram, '_configured_providers', return_value=[]), \
             mock.patch.object(self.telegram, '_handle_ask_callback') as answer, \
             mock.patch.object(self.telegram, '_edit_interactive') as edit:
            self.telegram._handle_callback('fixture', {'id': 'preview', 'data': 'ui-preview',
                'message': {'chat': {'id': 1}, 'message_id': 41}}, sessions)
        api.assert_called_once_with('fixture', 'answerCallbackQuery', timeout=10, callback_query_id='preview')
        answer.assert_not_called()
        edit.assert_not_called()
        sessions.switch_provider.assert_not_called()

    def test_owned_callbacks_return_full_choices_and_retire_keyboard(self):
        cases = [('Confirm this NFT transaction?', ('Yes, Mint now!', 'Not now'), 1),
                 ('Choose a language?', ('Python', 'Go', 'Rust', 'Java'), 2)]
        for question, options, selected in cases:
            with self.subTest(question=question):
                calls, result, ready = [], {}, threading.Event()
                def api(_api, method, **payload):
                    calls.append((method, payload))
                    return {'ok': True, 'result': {'message_id': 41}}
                def renderer(entry):
                    self.telegram._render_ask_question('fixture', 1, entry)
                    ready.set()
                self.interaction.register_channel('telegram:1', renderer)
                with mock.patch.object(self.telegram, '_api_call', side_effect=api):
                    worker = threading.Thread(target=lambda: result.update(answer=
                        self.interaction.ask('telegram:1', question, options)), daemon=True)
                    worker.start()
                    try:
                        self.assertTrue(ready.wait(2))
                        entry = self.interaction.pending('telegram:1')
                        self.assertIsNotNone(entry)
                        card = next(p for m, p in calls if m == 'sendMessage' and 'reply_markup' in p)
                        button = card['reply_markup']['inline_keyboard'][selected][0]
                        self.telegram._handle_ask_callback('fixture', 1, 41, button['callback_data'])
                        worker.join(2)
                        self.assertFalse(worker.is_alive())
                        self.assertEqual(result['answer'], options[selected])
                        receipts = [p for m, p in calls if m == 'editMessageText' and 'Answer:' in p.get('text', '')]
                        self.assertTrue(receipts)
                        self.assertIn('Answer: ' + options[selected], receipts[-1]['text'])
                        self.assertEqual(receipts[-1]['reply_markup'], {'inline_keyboard': []})
                    finally:
                        self.interaction.cancel('telegram:1')
                        worker.join(2)
                        self.interaction.unregister_channel('telegram:1')

    def test_plain_trailing_yes_no_marker_uses_owned_picker_with_full_context(self):
        from zeline.question_picker import branching_question
        for content in ['Continue? (y/n)', 'Lanjut? (Y/N)',
                        'Mint costs 0.002 ETH.\n\nProceed? (yes/no)']:
            with self.subTest(content=content):
                found = branching_question(content)
                self.assertIsNotNone(found)
                question, options = found
                self.assertEqual(options, ('Yes', 'No'))
                self.assertIn(content.split('?')[0], question)
                buttons = self.buttons(question, options)
                self.assertEqual(buttons[0]['text'], 'Yes')
                self.assertEqual(buttons[0]['style'], 'success' if 'ETH' in content else 'primary')
        self.assertIsNone(branching_question('Documentation: the script prints "Continue? (y/n)".'))
        self.assertIsNone(branching_question('x' * 510 + '\nProceed? (y/n)'))

    def test_transaction_approval_is_green_and_rejection_stays_red(self):
        cases = [('Mint this NFT for gas only?', 'Yes, Mint now!', 'Not now'),
                 ('List this NFT at 0.01 ETH?', 'List now', 'Cancel'),
                 ('Sell the token for USDC?', 'Sell now', 'No'),
                 ('Confirm this crypto transaction?', 'Confirm', 'Cancel'),
                 ('Transfer 1 USDT to this wallet?', 'Yes', 'Not now'),
                 ('Swap 1 USDC for ETH?', 'Swap now', 'Cancel'),
                 ('Pay this invoice in rupiah?', 'Confirm', 'Cancel')]
        for question, positive, negative in cases:
            with self.subTest(question=question):
                buttons = self.buttons(question, [positive, negative])
                self.assertEqual([b['text'] for b in buttons], [positive, negative])
                self.assertEqual([b.get('style') for b in buttons], ['success', 'danger'])

    def test_ordinary_choices_keep_letters_and_neutral_backgrounds(self):
        for options in [('Python', 'Go'), ('A', 'B', 'C', 'D'),
                        ('Yes', 'No', 'Preview', 'Something else'),
                        ('Yes, but do not mint anything', 'No'),
                        ('Yes — ' + 'a long explanation ' * 9, 'No'), ()]:
            with self.subTest(options=options):
                buttons = self.buttons('Which option do you prefer?', options)
                self.assertEqual([b['text'] for b in buttons],
                                 [chr(65+i) for i in range(len(options))] + ['Fill in yourself'])
                self.assertTrue(all('style' not in b for b in buttons))

    def test_financial_mentions_without_transaction_are_not_green(self):
        for question in ['Show token balance?', 'List files in this directory?',
                         'Enable wallet notifications?', 'Explain crypto transactions?']:
            with self.subTest(question=question):
                buttons = self.buttons(question, ['Yes', 'No'])
                self.assertEqual([b.get('style') for b in buttons], ['primary', 'danger'])

    def test_conventional_approval_pairs_preserve_original_order_and_words(self):
        for positive, negative in [('Confirm', 'Cancel'), ('Continue', 'Not now'),
                                   ('Setuju', 'Tidak'), ('Lanjutkan', 'Batal'),
                                   ('Approve', 'Reject')]:
            with self.subTest(pair=(positive, negative)):
                buttons = self.buttons('Update the description?', [negative, positive])
                self.assertEqual([b['text'] for b in buttons], [negative, positive])
                self.assertEqual([b.get('style') for b in buttons], ['danger', 'primary'])
                self.assertTrue(buttons[0]['callback_data'].endswith(':0'))

    def test_clear_yes_no_has_direct_blue_and_red_labels(self):
        buttons = self.buttons('Continue updating the description?', ['Yes', 'No'])
        self.assertEqual([b['text'] for b in buttons], ['Yes', 'No'])
        self.assertEqual([b.get('style') for b in buttons], ['primary', 'danger'])
        self.assertEqual([b['callback_data'] for b in buttons],
                         ['ask:1:owned-request:0', 'ask:1:owned-request:1'])
