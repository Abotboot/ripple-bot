"""Offline regression checks: python -m unittest -v test_bot."""
import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import meeting_tracker
import groq_engine
import ripple_bot_gateway as bot
from voice_capture import MeetingRecorder
import music_player


class TrackerTests(unittest.TestCase):
    def test_windows_key_file_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'GROQ_API_KEY': ''}):
            path = Path(tmp) / 'key.txt'
            path.write_text('gsk_test_key', encoding='utf-8-sig')
            with patch.object(groq_engine, 'KEY_FILE', str(path)):
                self.assertEqual(groq_engine.get_groq_key(), 'gsk_test_key')

    def test_mute_does_not_reset_duration_and_end_counts_once(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(meeting_tracker, 'STATS_FILE', tmp + '/stats.json'):
            tracker = meeting_tracker.MeetingTracker('99', None)
            vc = meeting_tracker.FOUNDERS_VC_ID
            with patch('meeting_tracker.time.time', return_value=100):
                tracker.start_meeting('Founder', [{'user_id': '1', 'username': 'A'}])
            with patch('meeting_tracker.time.time', return_value=120):
                tracker.on_voice_state_update('1', 'A', 'A', vc, vc)
            with patch('meeting_tracker.time.time', return_value=160):
                tracker.on_voice_state_update('1', 'A', 'A', vc, None)
            self.assertEqual(tracker.attendees['1']['total_seconds'], 60)
            with patch('meeting_tracker.time.time', return_value=221):
                self.assertTrue(tracker.check_auto_end())
                ok, embeds, _ = tracker.end_meeting()
            self.assertTrue(ok)
            self.assertIsInstance(embeds[0], dict)
            self.assertEqual(tracker.stats['members']['1']['total_seconds'], 60)
            self.assertFalse(tracker.end_meeting()[0])
            with open(tmp + '/stats.json', encoding='utf-8') as saved:
                self.assertEqual(json.load(saved)['total_meetings'], 1)

    def test_untrusted_downloads_rejected_before_network(self):
        for url in ('http://127.0.0.1/', 'https://169.254.169.254/', 'https://cdn.discordapp.com.evil.test/x'):
            with self.assertRaises(ValueError):
                bot.download_media(url)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_message_event_reaches_text_command(self):
        from discord.gateway import DiscordWebSocket
        events = []
        gateway = SimpleNamespace(_dispatch=lambda name, data: events.append((name, data)))
        message = {'id': '1', 'channel_id': '2', 'author': {'id': '3'}, 'content': '!meeting status'}
        DiscordWebSocket.debug_log_receive(gateway, json.dumps({'op': 0, 't': 'MESSAGE_CREATE', 'd': message}))
        with patch.object(bot, 'handle_message', AsyncMock()) as handler:
            for event, data in events:
                await getattr(bot.client, 'on_' + event)(data)
            handler.assert_awaited_once_with(message)
            await bot.client.on_socket_raw_receive(json.dumps({'op': 11, 'd': None}))
            handler.assert_awaited_once()

    async def test_login_429_waits_instead_of_crashing(self):
        response = SimpleNamespace(status=429, reason='Too Many Requests', headers={'Retry-After': '600'})
        fake_client = AsyncMock()
        fake_client.is_closed = Mock(return_value=False)
        fake_client.start = AsyncMock(side_effect=[discord.HTTPException(response, 'rate limited'), None])
        with patch.object(bot, 'TOKEN', 'test'), patch.object(bot, 'client', fake_client), patch.object(bot, 'start_health_server', AsyncMock()), patch.object(bot.asyncio, 'sleep', AsyncMock()) as sleep:
            await bot.run_bot()
        sleep.assert_awaited_once_with(600)
        self.assertEqual(fake_client.start.await_count, 2)

    async def test_meeting_deferred_before_work(self):
        order = []
        async def defer(**kwargs):
            order.append('defer')
        async def command(*args):
            order.append('work')
            return {'content': 'ok'}
        interaction = SimpleNamespace(user=object(), response=SimpleNamespace(is_done=lambda: False, defer=defer), edit_original_response=AsyncMock())
        with patch.dict(bot._interactions, {'test': interaction}), patch.object(bot, 'can_use_meeting', AsyncMock(return_value=True)), patch.object(bot, 'meeting_command', command):
            await bot.handle_interaction({'id': '1', 'token': 'test', 'channel_id': '1', 'data': {'name': 'meeting', 'options': [{'name': 'status'}]}})
        self.assertEqual(order, ['defer', 'work'])
        interaction.edit_original_response.assert_awaited_once()

    async def test_failed_ack_never_starts_meeting(self):
        interaction = SimpleNamespace(user=object(), response=SimpleNamespace(is_done=lambda: False, defer=AsyncMock(side_effect=TimeoutError)))
        with patch.dict(bot._interactions, {'test': interaction}), patch.object(bot, 'meeting_command', AsyncMock()) as command:
            await bot.handle_interaction({'id': '1', 'token': 'test', 'channel_id': '1', 'data': {'name': 'meeting'}})
        command.assert_not_awaited()

    async def test_waiting_http_does_not_block_other_commands(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def request(route, **kwargs):
            entered.set()
            await release.wait()
            return {'id': '1'}
        with patch.object(bot.client.http, 'request', request), patch.object(bot, '_meeting_lock', asyncio.Lock()):
            pending = asyncio.create_task(bot.api_call('/channels/1/messages', 'POST', {'content': 'hello'}))
            await asyncio.wait_for(entered.wait(), 0.5)
            response = await asyncio.wait_for(bot.meeting_command('status', 'A'), 0.5)
            self.assertIn('embeds', response)
            release.set()
            await pending

    async def test_concurrent_ends_produce_one_flat_report(self):
        channel = SimpleNamespace(send=AsyncMock())
        with tempfile.TemporaryDirectory() as tmp, patch.object(meeting_tracker, 'STATS_FILE', tmp + '/stats.json'):
            tracker = meeting_tracker.MeetingTracker('99', None)
            with patch.object(bot, 'tracker', tracker), patch.object(bot, 'recorder', None), patch.object(bot, '_meeting_lock', asyncio.Lock()), patch.object(bot.client, 'get_channel', return_value=channel):
                tracker.start_meeting('A', [{'user_id': '1', 'username': 'A'}])
                await asyncio.gather(bot.finish_meeting(True), bot.finish_meeting())
            self.assertEqual(tracker.stats['total_meetings'], 1)
            channel.send.assert_awaited_once()
            sent = channel.send.call_args.kwargs
            self.assertIsInstance(sent['embeds'][0], discord.Embed)
            self.assertEqual(sent['file'].filename, 'meeting-stats.json')

    async def test_voice_decrypt_decode_and_whisper(self):
        tracker = meeting_tracker.MeetingTracker('99', None)
        tracker.start_meeting('A')
        capture = MeetingRecorder(tracker)
        session = SimpleNamespace(ready=True, decrypt=Mock(return_value=b'opus'))
        capture._voice_client = SimpleNamespace(_connection=SimpleNamespace(dave_session=session, dave_protocol_version=1))
        decoder = SimpleNamespace(decode=Mock(return_value=b'\x00\x10' * 9600))
        user = SimpleNamespace(id=1, bot=False, display_name='A')
        data = SimpleNamespace(opus=b'encrypted')
        with patch('voice_capture.discord.opus.Decoder', return_value=decoder):
            capture.write(user, data)
        session.decrypt.assert_called_once()
        decoder.decode.assert_called_once_with(b'opus', fec=False)
        def transcribe(audio, filename):
            with wave.open(io.BytesIO(audio)) as wav:
                self.assertEqual(wav.getframerate(), 16000)
                self.assertEqual(wav.getnchannels(), 1)
            return 'Ship the update tomorrow.'
        with patch('voice_capture.groq_engine.groq_transcribe_audio', side_effect=transcribe):
            await capture.flush()
        self.assertIn('Ship the update tomorrow.', tracker.transcript_lines[0])
        self.assertEqual(capture.transcribed, 1)
        session.decrypt.side_effect = ValueError('bad ciphertext')
        capture.write(user, data)
        self.assertEqual(capture.dropped, 1)
        decoder.decode.assert_called_once()
        capture.cleanup()

    async def test_music_queue_fifo_and_controls(self):
        client = SimpleNamespace()
        queue = music_player.GuildMusicQueue("123", client)
        vc = SimpleNamespace(
            is_connected=Mock(return_value=True),
            is_playing=Mock(return_value=False),
            is_paused=Mock(return_value=False),
            play=Mock(),
            stop=Mock(),
            pause=Mock(),
            resume=Mock(),
        )
        t1 = music_player.MusicTrack(title="Song 1", artist="Artist 1", source_url="https://song1", stream_url="http://audio1", duration=125, thumbnail="", requester="UserA")
        t2 = music_player.MusicTrack(title="Song 2", artist="Artist 2", source_url="https://song2", stream_url="http://audio2", duration=60, thumbnail="", requester="UserB")
        self.assertEqual(t1.format_duration(), "02:05")

        with patch('music_player.discord.FFmpegPCMAudio'), patch('music_player.discord.PCMVolumeTransformer'):
            pos1 = await queue.enqueue(t1, vc)
            self.assertEqual(pos1, 1)
            self.assertEqual(queue.now_playing.title, "Song 1")
            vc.is_playing.return_value = True

            pos2 = await queue.enqueue(t2, vc)
            self.assertEqual(pos2, 1)
            self.assertEqual(len(queue.queue), 1)
            self.assertEqual(queue.queue[0].title, "Song 2")

            queue.pause()
            vc.pause.assert_called_once()
            vc.is_paused.return_value = True
            vc.is_playing.return_value = False

            queue.resume()
            vc.resume.assert_called_once()

            vol = queue.set_volume(150)
            self.assertEqual(vol, 1.0)
            vol_low = queue.set_volume(-10)
            self.assertEqual(vol_low, 0.01)

            self.assertTrue(queue.skip())
            vc.stop.assert_called_once()

            queue.stop()
            self.assertEqual(len(queue.queue), 0)
            self.assertIsNone(queue.now_playing)

    async def test_join_and_leave_voice_commands(self):
        fake_vc = SimpleNamespace(id=999, name="Test VC", is_connected=Mock(return_value=True), disconnect=AsyncMock(), move_to=AsyncMock(), connect=AsyncMock())
        fake_vc.connect = AsyncMock(return_value=fake_vc)
        fake_member = SimpleNamespace(id="123", voice=SimpleNamespace(channel=fake_vc))
        fake_guild = SimpleNamespace(id=1545531421081346101, get_member=Mock(return_value=fake_member), voice_channels=[fake_vc])

        with patch.object(bot.client, 'get_guild', return_value=fake_guild), patch.object(bot.discord.utils, 'get', return_value=None):
            res = await bot.execute_music_command('join', '', '123', 'User', '1')
            self.assertIn('Joined', res['content'])

        with patch.object(bot.client, 'get_guild', return_value=fake_guild), patch.object(bot.discord.utils, 'get', return_value=fake_vc):
            res_leave = await bot.execute_music_command('leave', '', '123', 'User', '1')
            self.assertIn('Left voice channel', res_leave['content'])
            fake_vc.disconnect.assert_awaited_once()

    async def test_music_search_and_selection_view(self):
        sample_tracks = [
            music_player.MusicTrack(title="Song 1", artist="Artist 1", source_url="https://s1", stream_url="http://a1", duration=120, thumbnail="", requester="User"),
            music_player.MusicTrack(title="Song 2", artist="Artist 2", source_url="https://s2", stream_url="http://a2", duration=180, thumbnail="", requester="User"),
        ]
        with patch('music_player.search_tracks', AsyncMock(return_value=sample_tracks)):
            res = await bot.execute_music_command('search', 'yeat', '123', 'User', '1')
            self.assertIn('embeds', res)
            self.assertEqual(len(res['embeds'][0]['fields']), 2)
            self.assertIn('view', res)
            components = res['view'].to_components()
            self.assertEqual(len(components[0]['components']), 2)
            self.assertEqual(components[0]['components'][0]['custom_id'], 'mselect:0')
            self.assertEqual(components[0]['components'][1]['custom_id'], 'mselect:1')

    async def test_music_play_numeric_quick_select(self):
        sample_tracks = [
            music_player.MusicTrack(title="Song 1", artist="Artist 1", source_url="https://s1", stream_url="http://a1", duration=120, thumbnail="", requester="User"),
            music_player.MusicTrack(title="Song 2", artist="Artist 2", source_url="https://s2", stream_url="http://a2", duration=180, thumbnail="", requester="User"),
        ]
        bot.recent_searches['1:123'] = sample_tracks
        with patch.object(bot, 'play_resolved_track', AsyncMock(return_value={'content': 'played'})) as mock_play:
            res = await bot.execute_music_command('play', '2', '123', 'User', '1')
            self.assertEqual(res['content'], 'played')
            mock_play.assert_awaited_once()
            self.assertEqual(mock_play.call_args[0][0].title, "Song 2")

    async def test_meeting_start_silent_vc_join(self):
        fake_vc = SimpleNamespace(id=1545542223381274634, members=[SimpleNamespace(id=1, name='A', display_name='A', bot=False)], send=AsyncMock())
        fake_recorder = SimpleNamespace(start=AsyncMock(), status=Mock(return_value="Idle"))
        old_rec = bot.recorder
        try:
            with patch.object(bot.client, 'get_channel', return_value=fake_vc), \
                 patch('groq_engine.get_groq_key', return_value='test_key'), \
                 patch.object(bot.tracker, 'is_active', False), \
                 patch.object(bot.tracker, 'start_meeting'), \
                 patch('ripple_bot_gateway.MeetingRecorder', return_value=fake_recorder):
                res = await bot.meeting_command('start', 'Founder', target_vc=fake_vc)
                self.assertIn('Meeting started', res['content'])
                fake_vc.send.assert_not_called()
        finally:
            bot.recorder = old_rec


if __name__ == '__main__':
    unittest.main()
