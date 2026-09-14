"""
Meeting Tracker for RippleBot
Tracks meeting hours and attendee presence exclusively for Founders VC.
Persists cumulative statistics and posts rich summaries to #📊｜meeting-reports.
"""

import os
import json
import time
from datetime import datetime
import groq_engine

FOUNDERS_CATEGORY_ID = "1545539256209121351"
FOUNDERS_VC_ID = "1545542415035797616"
REPORTS_CHANNEL_ID = "1547752670046068907"
STATS_FILE = os.path.join(os.path.dirname(__file__), "meeting_stats.json")

def format_duration(seconds: float) -> str:
    secs = int(max(0, seconds))
    hours = secs // 3600
    minutes = (secs % 3600) // 60
    rem_secs = secs % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{rem_secs}s")
    return " ".join(parts)

def format_hours_decimal(seconds: float) -> str:
    hrs = seconds / 3600.0
    return f"{hrs:.2f} hrs"

class MeetingTracker:
    def __init__(self, bot_id: str, api_call_fn):
        self.bot_id = str(bot_id)
        self.api_call = api_call_fn
        self.is_active = False
        self.start_time = 0.0
        self.started_by = ""
        self.attendees = {}  # uid -> {username, display_name, joined_at, total_seconds, currently_in}
        self.transcript_lines = []  # List of "Speaker: Text" strings
        self.empty_since = None
        self.reports_channel_id = REPORTS_CHANNEL_ID
        self.active_vc_id = FOUNDERS_VC_ID
        self.stats = self._load_stats()

    def _load_stats(self) -> dict:
        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[MeetingTracker] Error loading stats: {e}")
        return {
            "total_meetings": 0,
            "total_seconds": 0,
            "members": {},
            "history": []
        }

    def _save_stats(self):
        try:
            with open(STATS_FILE + '.tmp', "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2)
            os.replace(STATS_FILE + '.tmp', STATS_FILE)
        except Exception as e:
            print(f"[MeetingTracker] Error saving stats: {e}")

    async def ensure_reports_channel(self, guild_id: str) -> str:
        """Finds or creates the #📊｜meeting-reports channel in Founders category."""
        try:
            channels = await self.api_call(f"/guilds/{guild_id}/channels")
            if channels:
                for c in channels:
                    if "meeting-report" in c.get("name", "").lower() and c.get("parent_id") == FOUNDERS_CATEGORY_ID:
                        self.reports_channel_id = c["id"]
                        return self.reports_channel_id

            # Create if not found
            created = await self.api_call(f"/guilds/{guild_id}/channels", method="POST", data={
                "name": "📊｜meeting-reports",
                "type": 0,
                "parent_id": FOUNDERS_CATEGORY_ID,
                "topic": "Automated meeting logs and hour reports for Founders VC"
            })
            if created and "id" in created:
                self.reports_channel_id = created["id"]
                return self.reports_channel_id
        except Exception as e:
            print(f"[MeetingTracker] ensure_reports_channel error: {e}")
        return self.reports_channel_id

    def start_meeting(self, started_by_name: str, initial_voice_members: list = None, vc_id: str = None) -> tuple[bool, str]:
        if self.is_active:
            elapsed = time.time() - self.start_time
            return False, f"⚠️ A meeting is already active in <#{self.active_vc_id}> (running for **{format_duration(elapsed)}**)."

        self.is_active = True
        self.start_time = time.time()
        self.started_by = started_by_name
        self.active_vc_id = str(vc_id) if vc_id else FOUNDERS_VC_ID
        self.attendees = {}
        self.transcript_lines = []
        self.empty_since = None

        if initial_voice_members:
            for m in initial_voice_members:
                uid = str(m.get("user_id"))
                if uid == self.bot_id:
                    continue
                username = m.get("username", "Member")
                display_name = m.get("display_name", username)
                self.attendees[uid] = {
                    "username": username,
                    "display_name": display_name,
                    "joined_at": self.start_time,
                    "total_seconds": 0.0,
                    "currently_in": True
                }

        if not self.attendees:
            self.empty_since = self.start_time
        return True, f"🎙️ **Meeting Started!**\nTracking attendance and hours in <#{self.active_vc_id}>. Type `/meeting end` or `!meeting end` when done."

    def add_transcript(self, speaker: str, text: str):
        if not self.is_active or not text:
            return
        clean_text = text.strip()
        if clean_text:
            self.transcript_lines.append(f"{speaker}: {clean_text}")

    def on_voice_state_update(self, user_id: str, username: str, display_name: str, old_channel_id: str, new_channel_id: str):
        if not self.is_active:
            return

        user_id = str(user_id)
        if user_id == self.bot_id:
            return

        # Muting, deafening and streaming do not restart an attendance interval.
        if old_channel_id == new_channel_id:
            return

        now = time.time()

        # Joined active VC
        if new_channel_id == self.active_vc_id:
            self.empty_since = None
            if user_id not in self.attendees:
                self.attendees[user_id] = {
                    "username": username,
                    "display_name": display_name,
                    "joined_at": now,
                    "total_seconds": 0.0,
                    "currently_in": True
                }
            else:
                self.attendees[user_id]["joined_at"] = now
                self.attendees[user_id]["currently_in"] = True
                self.attendees[user_id]["display_name"] = display_name or self.attendees[user_id]["display_name"]
            print(f"[MeetingTracker] {username} joined active VC at {int(now)}")

        # Left active VC
        elif old_channel_id == self.active_vc_id and new_channel_id != self.active_vc_id:
            if user_id in self.attendees and self.attendees[user_id]["currently_in"]:
                joined_at = self.attendees[user_id].get("joined_at", now)
                delta = max(0.0, now - joined_at)
                self.attendees[user_id]["total_seconds"] += delta
                self.attendees[user_id]["currently_in"] = False
                print(f"[MeetingTracker] {username} left active VC (+{int(delta)}s, total: {int(self.attendees[user_id]['total_seconds'])}s)")

            # Check if all human members left
            still_in = [uid for uid, a in self.attendees.items() if a["currently_in"]]
            if not still_in:
                self.empty_since = now
                print(f"[MeetingTracker] Active VC is empty. Auto-end countdown started.")

    def check_auto_end(self, grace_period_secs: int = 60) -> bool:
        """Returns True if the VC has been empty longer than grace_period_secs."""
        if not self.is_active or self.empty_since is None:
            return False
        return (time.time() - self.empty_since) >= grace_period_secs

    def end_meeting(self) -> tuple[bool, dict, str]:
        """Ends current meeting, compiles embed report, updates stats."""
        if not self.is_active:
            return False, None, "⚠️ No active meeting in Founders VC to end."

        self.is_active = False

        now = time.time()
        meeting_duration = max(1.0, now - self.start_time)

        # Finalize open durations for anyone still in the call
        for uid, a in self.attendees.items():
            if a.get("currently_in", False):
                delta = max(0.0, now - a.get("joined_at", now))
                a["total_seconds"] += delta
                a["currently_in"] = False

        # Update persistent stats
        self.stats["total_meetings"] += 1
        self.stats["total_seconds"] += int(meeting_duration)

        for uid, a in self.attendees.items():
            if uid not in self.stats["members"]:
                self.stats["members"][uid] = {
                    "username": a["username"],
                    "display_name": a["display_name"],
                    "total_seconds": 0,
                    "meetings_attended": 0
                }
            m_stat = self.stats["members"][uid]
            m_stat["total_seconds"] += int(a["total_seconds"])
            m_stat["meetings_attended"] += 1
            m_stat["username"] = a["username"]
            m_stat["display_name"] = a["display_name"]

        # Add to history
        meeting_record = {
            "id": self.stats["total_meetings"],
            "started_by": self.started_by,
            "start_timestamp": int(self.start_time),
            "end_timestamp": int(now),
            "duration_seconds": int(meeting_duration),
            "attendees_count": len(self.attendees),
            "attendees": {
                uid: {
                    "username": a["username"],
                    "display_name": a["display_name"],
                    "seconds": int(a["total_seconds"])
                }
                for uid, a in self.attendees.items()
            }
        }
        self.stats["history"].append(meeting_record)
        self._save_stats()

        # Generate AI Executive Summary if notes/transcripts were captured
        ai_summary = ""
        if self.transcript_lines:
            try:
                raw_transcript = "\n".join(self.transcript_lines)
                att_names = [a.get("display_name") or a.get("username") for a in self.attendees.values()]
                ai_summary = groq_engine.groq_meeting_summary(
                    transcript=raw_transcript,
                    meeting_duration_str=format_duration(meeting_duration),
                    attendees_str=", ".join(att_names)
                )
            except Exception as e:
                print(f"[MeetingTracker] AI summary error: {e}")

        # Build Discord Embed(s)
        embeds = self._build_report_embeds(meeting_record, meeting_duration, ai_summary=ai_summary)

        # Reset state
        self.is_active = False
        self.start_time = 0.0
        self.started_by = ""
        self.attendees = {}
        self.transcript_lines = []
        self.empty_since = None
        self.active_vc_id = FOUNDERS_VC_ID

        summary_text = f"✅ **Founders Meeting Ended!** Duration: **{format_duration(meeting_duration)}**. Full report sent to <#{self.reports_channel_id}>."
        return True, embeds, summary_text

    def _build_report_embeds(self, record: dict, meeting_duration: float, ai_summary: str = "") -> list[dict]:
        start_ts = record["start_timestamp"]
        end_ts = record["end_timestamp"]
        duration_str = format_duration(meeting_duration)
        duration_hrs = format_hours_decimal(meeting_duration)

        # Build Attendees Breakdown
        attendee_lines = []
        sorted_attendees = sorted(record["attendees"].items(), key=lambda x: x[1]["seconds"], reverse=True)

        for uid, a in sorted_attendees:
            sec = a["seconds"]
            pct = (sec / meeting_duration * 100.0) if meeting_duration > 0 else 0.0
            time_str = format_duration(sec)
            all_time_sec = self.stats["members"].get(uid, {}).get("total_seconds", sec)
            all_time_hrs = format_hours_decimal(all_time_sec)
            name = a.get("display_name") or a.get("username")
            attendee_lines.append(f"• <@{uid}> (**{name}**): `{time_str}` ({pct:.0f}%) — *All-time: {all_time_hrs}*")

        if not attendee_lines:
            attendee_lines.append("*No attendees detected in VC.*")

        # Top 3 cumulative founders
        top_founders = sorted(self.stats["members"].items(), key=lambda x: x[1]["total_seconds"], reverse=True)[:3]
        leaderboard_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, (f_uid, f_stat) in enumerate(top_founders):
            medal = medals[idx] if idx < len(medals) else "•"
            f_name = f_stat.get("display_name") or f_stat.get("username")
            hrs = format_hours_decimal(f_stat["total_seconds"])
            leaderboard_lines.append(f"{medal} <@{f_uid}> (**{f_name}**): **{hrs}** ({f_stat['meetings_attended']} meetings)")

        fields = [
            {
                "name": "⏱️ Meeting Duration",
                "value": f"**{duration_str}** (`{duration_hrs}`)",
                "inline": True
            },
            {
                "name": "🕒 Start Time",
                "value": f"<t:{start_ts}:t> (<t:{start_ts}:R>)",
                "inline": True
            },
            {
                "name": "🏁 End Time",
                "value": f"<t:{end_ts}:t>",
                "inline": True
            },
            {
                "name": f"👥 Attendees ({len(sorted_attendees)})",
                "value": "\n".join(attendee_lines)[:1024],
                "inline": False
            }
        ]

        if leaderboard_lines:
            fields.append({
                "name": "🏆 Cumulative Founders Leaderboard",
                "value": "\n".join(leaderboard_lines)[:1024],
                "inline": False
            })

        main_embed = {
            "title": f"📊 Meeting #{record['id']} Report",
            "description": f"Official meeting record for <#{self.active_vc_id}>.\nLogged by **RippleBot**.",
            "color": 0x0ea5e9,  # Ripple Cyan / Sky Blue
            "fields": fields,
            "footer": {
                "text": f"A Ripple Effect • Total Meetings Held: {self.stats['total_meetings']} | Total Hours: {format_hours_decimal(self.stats['total_seconds'])}"
            },
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        result_embeds = [main_embed]

        if ai_summary and ai_summary.strip():
            summary_embed = {
                "title": f"🎯 Founders Meeting #{record['id']} — AI Executive Brief",
                "description": ai_summary[:4000],
                "color": 0x38bdf8
            }
            result_embeds.append(summary_embed)

        return result_embeds

    def get_status_embed(self) -> dict:
        if not self.is_active:
            return {
                "title": "🎙️ Founders Meeting Status",
                "description": "No active meeting currently in session. Start one with `/meeting start` or `!meeting start`.",
                "color": 0x64748b
            }

        now = time.time()
        elapsed = now - self.start_time
        lines = []
        for uid, a in self.attendees.items():
            status = "🟢 In VC" if a["currently_in"] else "⚪ Away"
            current_sec = a["total_seconds"]
            if a["currently_in"]:
                current_sec += max(0, now - a.get("joined_at", now))
            name = a.get("display_name") or a.get("username")
            lines.append(f"• {status} <@{uid}> (**{name}**): `{format_duration(current_sec)}`")

        if not lines:
            lines.append("*Waiting for members to join Founders VC...*")

        return {
            "title": "🎙️ Active Meeting Status",
            "description": f"Meeting in progress for <#{self.active_vc_id}>.\nStarted by **{self.started_by}** <t:{int(self.start_time)}:R>.",
            "color": 0x22c55e,
            "fields": [
                {
                    "name": "⏱️ Current Elapsed Time",
                    "value": f"**{format_duration(elapsed)}**",
                    "inline": True
                },
                {
                    "name": "👥 Active Participants",
                    "value": "\n".join(lines)[:1024],
                    "inline": False
                }
            ],
            "footer": {"text": "Type /meeting end or !meeting end to conclude & generate report."}
        }

    def get_stats_embed(self) -> dict:
        total_m = self.stats["total_meetings"]
        total_s = self.stats["total_seconds"]

        if total_m == 0:
            return {
                "title": "📊 All-Time Founders Meeting Stats",
                "description": "No meetings recorded yet! Run `/meeting start` when your team joins Founders VC.",
                "color": 0x0ea5e9
            }

        sorted_members = sorted(self.stats["members"].items(), key=lambda x: x[1]["total_seconds"], reverse=True)
        lines = []
        for idx, (uid, m) in enumerate(sorted_members, 1):
            hrs = format_hours_decimal(m["total_seconds"])
            dur = format_duration(m["total_seconds"])
            lines.append(f"**{idx}.** <@{uid}> (**{m.get('display_name') or m.get('username')}**): **{hrs}** (`{dur}`) across {m['meetings_attended']} meetings")

        return {
            "title": "📊 All-Time Founders Meeting Hours",
            "description": f"Cumulative statistics across **{total_m}** meeting(s) totaling **{format_hours_decimal(total_s)}**.",
            "color": 0x0ea5e9,
            "fields": [
                {
                    "name": "🏆 Hours Leaderboard",
                    "value": "\n".join(lines)[:1024] if lines else "*No member data.*",
                    "inline": False
                }
            ],
            "footer": {"text": "A Ripple Effect • Founders VC Meeting Tracker"}
        }
