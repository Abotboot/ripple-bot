"""Bounded voice capture using Discord's negotiated DAVE session and Groq Whisper."""
import asyncio
import audioop
import io
import logging
import threading
import time
import wave

import davey
import discord
from discord.ext import voice_recv

import groq_engine


class MeetingRecorder(voice_recv.AudioSink):
    # ponytail: one 30-second buffer per speaker, at most 20 speakers; add disk spooling for larger calls.
    MAX_BYTES = 48000 * 2 * 2 * 30

    def __init__(self, tracker):
        super().__init__()
        self.tracker = tracker
        self.meeting_id = tracker.start_time
        self.buffers = {}
        self.decoders = {}
        self.lock = threading.Lock()
        self.accepting = True
        self.received = 0
        self.dropped = 0
        self.transcribed = 0
        self.failures = 0
        self.worker = None
        self.vc = None

    def wants_opus(self):
        # Receive transport-decrypted frames. Apply DAVE before the Opus decoder.
        return True

    def write(self, user, data):
        if user is None or user.bot or not self.accepting or not data.opus:
            return
        with self.lock:
            if not self.accepting:
                return
            if user.id not in self.buffers and len(self.buffers) >= 20:
                self.dropped += 1
                return
            frame = bytes(data.opus)
            if frame == b'\xf8\xff\xfe':
                return
            connection = self.voice_client._connection
            session = connection.dave_session
            # Never downgrade encryption or decode ciphertext as audio.
            if connection.dave_protocol_version == 0 or session is None or not session.ready:
                self.dropped += 1
                return
            try:
                frame = session.decrypt(user.id, davey.MediaType.audio, frame)
                decoder = self.decoders.get(user.id)
                if decoder is None:
                    decoder = self.decoders[user.id] = discord.opus.Decoder()
                pcm = decoder.decode(frame, fec=False)
            except (ValueError, discord.opus.OpusError):
                self.dropped += 1
                return
            entry = self.buffers.setdefault(user.id, [time.time(), user.display_name, bytearray()])
            if len(entry[2]) + len(pcm) > self.MAX_BYTES:
                self.dropped += 1
                return
            entry[2].extend(pcm)
            self.received += 1

    def cleanup(self):
        with self.lock:
            self.accepting = False

    async def start(self, channel):
        # Fail at start if the host lacks libopus, rather than killing the receive thread later.
        discord.opus.Decoder()
        self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient, timeout=20, reconnect=True, self_deaf=False)
        try:
            self.vc.listen(self)
        except Exception:
            await self.vc.disconnect(force=True)
            raise
        self.worker = asyncio.create_task(self._run())

    def status(self):
        connected = self.vc and self.vc.is_connected() and self.vc.is_listening()
        state = 'Listening' if connected else 'Disconnected'
        return f'{state}; audio packets: {self.received}; transcript segments: {self.transcribed}; dropped packets: {self.dropped}; transcription failures: {self.failures}'

    async def _run(self):
        while self.accepting:
            await asyncio.sleep(15)
            await self.flush()

    async def flush(self):
        with self.lock:
            segments = sorted(self.buffers.values(), key=lambda item: item[0])
            self.buffers = {}
        for timestamp, speaker, pcm in segments:
            # Ignore tiny/silent chunks, which Whisper can hallucinate text from.
            if len(pcm) < 19200 or audioop.rms(pcm, 2) < 80:
                continue
            mono = audioop.tomono(bytes(pcm), 2, 0.5, 0.5)
            mono, _ = audioop.ratecv(mono, 2, 1, 48000, 16000, None)
            out = io.BytesIO()
            with wave.open(out, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(mono)
            text = await asyncio.to_thread(groq_engine.groq_transcribe_audio, out.getvalue(), 'meeting.wav')
            if text and self.tracker.is_active and self.tracker.start_time == self.meeting_id:
                elapsed = max(0, int(timestamp - self.meeting_id))
                self.tracker.add_transcript(f'[{elapsed // 60:02}:{elapsed % 60:02}] {speaker}', text)
                self.transcribed += 1
            elif not text:
                self.failures += 1
                logging.warning('Voice transcription returned no text')

    async def stop(self):
        self.cleanup()
        if self.vc:
            self.vc.stop_listening()
        # Let in-flight transcription finish; cancelling a thread would lose its text.
        if self.worker:
            await self.worker
        await self.flush()
        if self.vc:
            await self.vc.disconnect(force=True)
