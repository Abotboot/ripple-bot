import os
import sys
import time
import asyncio
import logging
import inspect
import gradio as gr
import ripple_bot_gateway

try:
    import spaces
    @spaces.GPU
    def gpu_pipeline_worker():
        return True
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def get_dashboard_metrics():
    is_ready = getattr(ripple_bot_gateway.client, "is_ready", lambda: False)()
    bot_status = "🟢 Connected & Active" if is_ready else "🟡 Connecting to Discord..."
    user_info = str(ripple_bot_gateway.client.user) if is_ready and ripple_bot_gateway.client.user else "Pending login"
    
    tracker = ripple_bot_gateway.tracker
    meeting_status = "🔴 Meeting in Progress" if tracker.is_active else "⚪ Idle (No active meeting)"
    channel = str(tracker.voice_channel_id) if tracker.is_active else "None"
    total_meetings = str(tracker.stats.get("total_meetings", 0))
    
    recorder = getattr(ripple_bot_gateway, "recorder", None)
    voice_status = recorder.status() if recorder else "Idle (Waiting for meeting)"
    
    return bot_status, user_info, meeting_status, channel, total_meetings, voice_status

with gr.Blocks(title="RippleBot Cloud Hub") as demo:
    gr.Markdown("# 🌊 RippleBot Cloud Hub\n24/7 Voice & Meeting Assistant on Hugging Face Spaces")
    with gr.Row():
        status_box = gr.Textbox(label="Gateway Status", value="Initializing...", interactive=False)
        user_box = gr.Textbox(label="Logged In As", value="Checking...", interactive=False)
    with gr.Row():
        meeting_box = gr.Textbox(label="Current Meeting", value="Checking...", interactive=False)
        channel_box = gr.Textbox(label="Voice Channel", value="Checking...", interactive=False)
    with gr.Row():
        meetings_box = gr.Textbox(label="Total Meetings Logged", value="0", interactive=False)
        voice_box = gr.Textbox(label="DAVE / Voice Pipeline", value="Checking...", interactive=False)
    refresh_btn = gr.Button("🔄 Refresh Status", variant="primary")
    refresh_btn.click(
        fn=get_dashboard_metrics,
        outputs=[status_box, user_box, meeting_box, channel_box, meetings_box, voice_box]
    )
    demo.load(
        fn=get_dashboard_metrics,
        outputs=[status_box, user_box, meeting_box, channel_box, meetings_box, voice_box]
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    launch_kwargs = {
        "server_name": "0.0.0.0",
        "server_port": port,
        "prevent_thread_lock": True
    }
    sig = inspect.signature(demo.launch)
    if "ssr_mode" in sig.parameters:
        launch_kwargs["ssr_mode"] = False
    if "ssr" in sig.parameters:
        launch_kwargs["ssr"] = False

    demo.queue()
    demo.launch(**launch_kwargs)
    logging.info("Gradio dashboard active on port %s. Launching RippleBot Discord gateway on main thread...", port)

    while True:
        try:
            asyncio.run(ripple_bot_gateway.run_bot())
        except Exception as e:
            logging.exception("Discord gateway encountered error: %s; restarting in 10s...", e)
            time.sleep(10)
