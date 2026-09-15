"""Offline checks for the upgrade: ! prefix delivery, automod, XP, extras commands, fast music."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import extras
import music_player
import ripple_bot_gateway as bot


def _fake_member(name='Tester', uid='123', **perms):
    defaults = dict(manage_messages=False, moderate_members=False, kick_members=False,
                    ban_members=False, manage_guild=False, manage_channels=False)
    defaults.update(perms)
    return SimpleNamespace(id=uid, name=name, display_name=name, bot=False,
                           guild_permissions=SimpleNamespace(**defaults), mention=f'<@{uid}>',
                           kick=AsyncMock(), ban=AsyncMock())


class ExtrasBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.api = AsyncMock()
        extras.DATA_FILE = str(Path(self.tmp.name) / 'extras.json')
        extras.setup(SimpleNamespace(), self.api, '555')
        self.channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(delete=AsyncMock())))
        extras.client.get_channel = Mock(return_value=self.channel)
        extras._guild = Mock(return_value=SimpleNamespace(
            id=555, get_member=Mock(return_value=_fake_member()), member_count=10))

    def tearDown(self):
        self.tmp.cleanup()


class ExtrasTests(ExtrasBase):
    async def test_automod_deletes_and_warns_on_invite(self):
        d = {'id': '1', 'channel_id': '2', 'content': 'join https://discord.gg/abc now',
             'author': {'id': '999', 'username': 'Spammer'}, 'mentions': []}
        with patch.object(extras.asyncio, 'sleep', AsyncMock()):
            handled = await extras.run_automod(d)
        self.assertTrue(handled)
        delete_calls = [c for c in self.api.await_args_list if c.kwargs.get('method') == 'DELETE']
        self.assertEqual(len(delete_calls), 1)
        self.assertIn('messages/1', delete_calls[0].args[0])
        self.assertEqual(len(extras._data['warnings']['999']), 1)

    async def test_automod_spares_moderators(self):
        member = _fake_member(manage_messages=True)
        extras._guild().get_member = Mock(return_value=member)
        d = {'id': '1', 'channel_id': '2', 'content': 'discord.gg/whatever',
             'author': {'id': '123', 'username': 'Mod'}, 'mentions': []}
        self.assertFalse(await extras.run_automod(d))
        self.api.assert_not_awaited()

    async def test_automod_detects_spam_caps_and_words(self):
        extras._data['automod']['words'] = ['heck']
        self.assertEqual(extras._automod_violation('u', 'what the heck', 0), 'language filter (`heck`)')
        self.assertEqual(extras._automod_violation('u', 'join my server', 9), 'mention spam')
        self.assertEqual(extras._automod_violation('u', 'A' * 40, 0), 'excessive caps')
        for _ in range(7):
            self.assertIsNone(extras._automod_violation('spammer', 'hi', 0))
        self.assertEqual(extras._automod_violation('spammer', 'hi', 0), 'sending messages too fast (spam)')

    async def test_xp_levels_up_and_respects_cooldown(self):
        first = extras.award_xp('7', 'Amy')
        self.assertIsNone(first)  # first award is below level 1 threshold
        self.assertIn('7', extras._data['xp'])
        self.assertIsNone(extras.award_xp('7', 'Amy'))  # cooldown
        rec = extras._data['xp']['7']
        # One XP point below the boundary into level 2 guarantees a level-up.
        rec['xp'] = extras._xp_for_level(0) + extras._xp_for_level(1) - 10
        extras._xp_cooldown['7'] = 0
        self.assertEqual(extras.award_xp('7', 'Amy'), 2)

    async def test_rank_and_leaderboard(self):
        extras._data['xp']['7'] = {'xp': sum(extras._xp_for_level(l) for l in range(3)), 'name': 'Amy'}
        rank = extras.cmd_rank('7')
        self.assertEqual(rank['embeds'][0]['fields'][0]['value'], '**3**')
        self.assertIn('Amy', extras.cmd_leaderboard()['content'])

    async def test_prefix_fun_commands(self):
        result = await extras.handle_prefix('!8ball will it work?', '1', 'U', '2', '3', None, None)
        self.assertIn('🎱', result['content'])
        self.assertIn('It', (await extras.handle_prefix('!flip', '1', 'U', '2', '3', None, None))['content'])
        result = await extras.handle_prefix('!choose pizza, sushi', '1', 'U', '2', '3', None, None)
        self.assertIn(result['content'], ('🤖 I choose **pizza**!', '🤖 I choose **sushi**!'))
        self.assertIsNone(await extras.handle_prefix('!note hello', '1', 'U', '2', '3', None, None))

    async def test_prefix_moderation_requires_permissions(self):
        plain = _fake_member()
        denied = await extras.handle_prefix('!kick <@555> bad', '123', 'U', '2', '3', plain,
                                            SimpleNamespace(permissions=plain.guild_permissions))
        self.assertIn('permission', denied['content'])

        mod = _fake_member(kick_members=True)
        allowed = await extras.handle_prefix('!kick <@555> being rude', '123', 'Mod', '2', '3', mod,
                                             SimpleNamespace(permissions=mod.guild_permissions))
        self.assertIn('was kicked', allowed['content'])

        warned = await extras.handle_prefix('!warn <@5555> being rude', '123', 'Mod', '2', '3',
                                            _fake_member(moderate_members=True),
                                            SimpleNamespace(permissions=plain.guild_permissions))
        self.assertIn('was warned', warned['content'])
        self.assertEqual(len(extras._data['warnings']['5555']), 1)

    async def test_reminder_duration_parse_and_flow(self):
        self.assertEqual(extras.parse_duration('1h30m'), 5400)
        self.assertEqual(extras.parse_duration('2d'), 172800)
        self.assertIsNone(extras.parse_duration('tomorrow'))
        await extras.add_reminder('7', 'Amy', '2', 60, 'stand up')
        self.assertEqual(len(extras._data['reminders']), 1)
        self.assertIn('stand up', extras.cmd_reminders('7')['content'])

    async def test_escalation_timeouts_repeat_offenders(self):
        for i in range(3):
            extras._add_warning('42', f'rule break {i}', 'AutoMod')
        escalation = await extras.escalate('42', 'Chronic')
        self.assertIn('timed out', escalation)
        patch_call = self.api.await_args
        self.assertEqual(patch_call.kwargs.get('method'), 'PATCH')
        self.assertIn('communication_disabled_until', patch_call.kwargs['data'])

    async def test_welcome_message_formats_template(self):
        extras._amod()['welcome_channel'] = '42'
        member = SimpleNamespace(id='9', name='newbie', display_name='Newbie', mention='<@9>',
                                 guild=SimpleNamespace(name='Ripple', member_count=42))
        await extras.on_member_join(member)
        sent = self.api.await_args
        self.assertEqual(sent.kwargs.get('method'), 'POST')
        self.assertIn('<@9>', sent.kwargs['data']['content'])
        self.assertIn('member #10', sent.kwargs['data']['content'])

    async def test_automod_slash_toggle(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id='1', display_name='Admin'), channel_id='2',
                                      permissions=SimpleNamespace(manage_guild=True))
        result = await extras.handle_slash(interaction, 'automod', {'_sub': 'toggle'})
        self.assertIn('OFF', result['content'])
        result = await extras.handle_slash(interaction, 'automod', {'_sub': 'toggle'})
        self.assertIn('ON', result['content'])


class MessagePathTests(unittest.IsolatedAsyncioTestCase):
    def test_message_payload_conversion(self):
        ref_msg = SimpleNamespace(
            id=11, channel=SimpleNamespace(id=2), guild=SimpleNamespace(id=3),
            content='original', author=SimpleNamespace(id='5', name='amy', global_name='Amy', bot=False),
            attachments=[], embeds=[], mentions=[], reference=None, referenced_message=None)
        message = SimpleNamespace(
            id=12, channel=SimpleNamespace(id=2), guild=SimpleNamespace(id=3),
            content='!play lofi', author=SimpleNamespace(id='4', name='zed', global_name=None, bot=False),
            attachments=[], embeds=[], mentions=[ref_msg.author],
            reference=SimpleNamespace(message_id=11, channel_id=2), referenced_message=ref_msg)
        payload = bot.message_payload(message)
        self.assertEqual(payload['id'], '12')
        self.assertEqual(payload['guild_id'], '3')
        self.assertEqual(payload['referenced_message']['content'], 'original')
        self.assertFalse(payload['author']['bot'])

    async def test_on_message_reaches_text_command_exactly_once(self):
        payload = {'id': '77', 'channel_id': '2', 'content': '!purge 3',
                   'author': {'id': '4', 'username': 'zed', 'global_name': None, 'bot': False},
                   'attachments': [], 'embeds': [], 'mentions': [],
                   'message_reference': None, 'referenced_message': None, 'guild_id': '3'}
        message = SimpleNamespace(id=77, author=SimpleNamespace(bot=False, id='4'))
        with patch.object(bot, 'handle_message', AsyncMock()) as handler, \
             patch.object(bot, 'message_payload', Mock(return_value=payload)):
            await bot.client.on_message(message)
            # The raw-socket fallback must not double-handle the same message.
            await bot.client.on_socket_raw_receive(json.dumps({'op': 0, 't': 'MESSAGE_CREATE', 'd': payload}))
            handler.assert_awaited_once_with(payload)

    async def test_on_message_ignores_bots_and_duplicates(self):
        message = SimpleNamespace(id=88, author=SimpleNamespace(bot=True, id='9'))
        with patch.object(bot, 'handle_message', AsyncMock()) as handler:
            await bot.client.on_message(message)
            handler.assert_not_awaited()


class SsrfTests(unittest.IsolatedAsyncioTestCase):
    def test_private_media_urls_rejected_before_network(self):
        for url in ('http://127.0.0.1/x.mp3', 'http://169.254.169.254/latest/meta-data',
                    'http://localhost:8080/', 'http://10.0.0.5/a', 'http://192.168.1.1/a',
                    'file:///etc/passwd', 'ftp://example.com/a'):
            with self.assertRaises(ValueError):
                music_player.assert_public_http_url(url)

    def test_public_media_urls_accepted(self):
        self.assertIsNone(music_player.assert_public_http_url('https://www.youtube.com/watch?v=dQw4w9WgXcQ'))
        self.assertIsNone(music_player.assert_public_http_url('https://open.spotify.com/track/abc'))

    def test_spotify_host_suffix_bypass_rejected(self):
        self.assertIsNone(music_player.resolve_spotify_url('https://open.spotify.com/track/abc'))
        # Substring-match bypass (open.spotify.com.evil.com) must not resolve
        self.assertIsNone(music_player.resolve_spotify_url('https://open.spotify.com.evil.com/track/abc'))
        self.assertIsNone(music_player.resolve_spotify_url('https://evil.com/open.spotify.com'))


class MusicSpeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_flat_listing_and_resolves_only_first(self):
        flat = [
            {'title': f'Song {i}', 'uploader': f'Artist {i}', 'duration': 100 + i,
             'webpage_url': f'https://youtube.com/watch?v=vid{i}', 'thumbnail': None}
            for i in range(5)
        ]
        extracted = {'url': 'http://stream', 'duration': 101}
        with patch.object(music_player, '_flat_entries_sync', Mock(return_value=flat)) as flat_mock, \
             patch.object(music_player, '_extract_info_sync', Mock(return_value=extracted)) as extract_mock:
            tracks = await music_player.search_tracks('lofi beats', 'User', limit=5)
        flat_mock.assert_called_once_with('lofi beats', 5)
        # Only the track that will play immediately gets fully resolved.
        self.assertEqual(extract_mock.call_count, 1)
        self.assertEqual(tracks[0].stream_url, 'http://stream')
        self.assertTrue(all(t.stream_url == '' for t in tracks[1:]))
        # Cached second lookup does not hit the extractor again.
        await music_player.search_tracks('lofi beats', 'User', limit=5)
        self.assertEqual(extract_mock.call_count, 1)

    async def test_queue_loop_replays_and_stop_breaks_it(self):
        queue = music_player.GuildMusicQueue('1', SimpleNamespace())
        vc = SimpleNamespace(is_connected=Mock(return_value=True), is_playing=Mock(return_value=False),
                             is_paused=Mock(return_value=False), play=Mock(), stop=Mock())
        track = music_player.MusicTrack(title='T', artist='A', source_url='https://s', stream_url='http://a',
                                        duration=10, thumbnail='', requester='U')
        with patch.object(music_player.discord, 'FFmpegPCMAudio', Mock()), \
             patch.object(music_player.discord, 'PCMVolumeTransformer', Mock()):
            queue.loop = True
            await queue.enqueue(track, vc)
            self.assertEqual(queue.now_playing.title, 'T')
            # When playback ends, loop reinserts the track so it replays.
            vc.is_playing.return_value = True
            after_cb = vc.play.call_args.kwargs['after']
            after_cb(None)
            await asyncio.sleep(0.05)
            self.assertEqual(queue.now_playing.title, 'T')
            queue.stop()
            self.assertEqual(queue.queue, [])
            self.assertIsNone(queue.now_playing)

    async def test_music_shuffle_and_remove(self):
        queue = music_player.GuildMusicQueue('1', SimpleNamespace())
        queue.queue = [music_player.MusicTrack(title='A', artist='', source_url='', stream_url='', duration=1, thumbnail='', requester='U'),
                       music_player.MusicTrack(title='B', artist='', source_url='', stream_url='', duration=1, thumbnail='', requester='U')]
        self.assertEqual(queue.remove(1).title, 'A')
        queue.shuffle()
        self.assertEqual([t.title for t in queue.queue], ['B'])
        self.assertIsNone(queue.remove(9))


if __name__ == '__main__':
    unittest.main()
