# 🌐 RippleBot 24/7 Free Cloud Hosting Guide

Run RippleBot 24/7 in the cloud with zero hosting fees and without keeping your PC awake.

---

## Option 1: Koyeb (Recommended — 100% Free Forever, Never Sleeps)

Koyeb offers a **Free Eco tier** that runs 24/7 continuously without sleeping or requiring a credit card.

1. **Push code to GitHub**:
   Create a new private GitHub repository and push this directory (`scratch/`).
2. **Sign up at Koyeb**: [koyeb.com](https://www.koyeb.com/) (Sign in with GitHub).
3. **Create Service**:
   - Click **Create Web Service**.
   - Choose **GitHub** and select your repository.
   - Select **Dockerfile** as the build method (or buildpack `pip install -r requirements.txt`).
   - Instance Type: **Eco Free** (Nano, 512MB RAM).
   - Port: `8080` (HTTP healthcheck is built-in to `ripple_bot_gateway.py`).
4. **Deploy**:
   Click **Deploy**. RippleBot connects to Discord within 60 seconds and stays online 24/7!

---

## Option 2: Render.com (100% Free Web Service)

Render provides a generous free tier for Python web services.

1. **Push to GitHub**.
2. **Sign up at Render**: [render.com](https://render.com/).
3. **New Web Service**:
   - Click **New +** -> **Web Service**.
   - Connect your GitHub repository.
   - Runtime: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python -u ripple_bot_gateway.py`
   - Instance Type: **Free**
4. **Keep-Alive (Prevent Sleep)**:
   - Render free web services go to sleep if inactive for 15 minutes.
   - Set up a free monitor at [UptimeRobot.com](https://uptimerobot.com/) or [cron-job.org](https://cron-job.org/) to ping your Render URL every 10 minutes:
     `https://your-bot-name.onrender.com/health`
   - This keeps your bot running 24/7 forever for $0!

---

## Option 3: Oracle Cloud Always Free VPS (True Dedicated Linux Server)

Oracle Cloud provides **2 Always-Free Linux virtual machines** that run forever with zero cost.

1. Create a free VM on Oracle Cloud.
2. SSH into your VM:
   ```bash
   git clone <your-repo-url>
   cd <your-repo>
   docker build -t ripplebot .
   docker run -d --restart=always --name ripplebot ripplebot
   ```
3. Docker will automatically keep the bot running and restart it if the server ever reboots.

---

## File Summary in this Repo
- `Dockerfile`: Production container with Debian, FFmpeg, and Python 3.12.
- `requirements.txt`: Python package dependencies.
- `Procfile`: Procfile for process-manager platforms.
- `render.yaml`: Blueprint configuration for Render.
