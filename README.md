# 📥 viddown — Video Downloader by RY9

**Download videos from YouTube, Instagram, TikTok — and hundreds of other sites.**  
Works on Windows, Mac, and Linux. No technical experience needed.

---

## 🙋 What does this tool do?

`viddown` is a tool you run in a **terminal** (a text window on your computer).  
You give it a video link, choose what you want, and it downloads the file for you.

You can:
- 📥 Download the **full video** in the quality you choose (1080p, 720p, 480p, etc.)
- ✂️ Download only a **short clip** from a video (e.g. from 5:23 to 6:44)
- 🎵 Download just the **audio** as an MP3 (great for music or podcasts)
- 🎙️ Download an **audio clip** from a specific part of a video
- 📦 Download **multiple videos at the same time** (parallel — they all download together)
- 🎶 Download **multiple videos as MP3 audio** at the same time
- ⏸️ **Pause or lose internet mid-download?** No problem — it resumes automatically from where it left off, no re-downloading needed
- 🔄 **Update** the downloader from inside the tool to support the latest sites

---

## ✅ What do you need to install?

> **Just one thing: Docker.**

That's it. You do **not** need to install Python, ffmpeg, yt-dlp, or anything else.  
Docker handles everything automatically behind the scenes.

### 🐳 How to install Docker

Click the link for your operating system:

| Your computer | Download link |
|---|---|
| 🪟 **Windows** | [Download Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) |
| 🍎 **Mac** | [Download Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) |
| 🐧 **Linux** | [Follow these steps](https://docs.docker.com/engine/install/) (pick your distro) |

After installing Docker Desktop, **open it** and leave it running in the background.  
You don't need to do anything with it — just keep it open.

---

## 📦 How to set up viddown (do this once)

### Step 1 — Download viddown

Download the `viddown` folder from this page and save it somewhere easy to find,  
for example your **Desktop** or your **Documents** folder.

### Step 2 — Open a terminal

**On Windows:**
1. Press the **Start** button
2. Search for **"Command Prompt"** or **"PowerShell"**
3. Click to open it

**On Mac:**
1. Press **⌘ Command + Space**
2. Type **"Terminal"**
3. Press **Enter**

**On Linux:**
Press **Ctrl + Alt + T** — that opens a terminal window.

### Step 3 — Go to the viddown folder

In the terminal, type this and press **Enter**  
(replace the path with wherever you saved it):

**Windows:**
```
cd C:\Users\YourName\Desktop\viddown
```

**Mac / Linux:**
```
cd ~/Desktop/viddown
```

> 💡 **Tip:** You can drag the `viddown` folder directly into the terminal window  
> and it will type the path for you automatically.

### Step 4 — Build the tool (first time only, takes ~5 minutes)

```
./dl --build
```

> ⚠️ **Windows users:** If `./dl` doesn't work, try:
> ```
> bash dl --build
> ```
> You may need [Git for Windows](https://git-scm.com/download/win) for `bash` to work.

You'll see text scrolling — that's normal. Wait for **"Image built successfully."**  
This only happens **once**. Every future download starts instantly.

---

## ▶️ How to download a video

### 1. Copy the video link

Go to YouTube, Instagram, or TikTok and copy the link from your browser.

### 2. Run the tool

```
./dl "PASTE-YOUR-LINK-HERE"
```

Example:
```
./dl "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

> 💡 Keep the quote marks `"` around the link.

### 3. Use the menu

An interactive menu appears. Use the **arrow keys** to navigate and **Enter** to select:

```
  viddown  —  What would you like to do?

  ❯  📥  Download full video        (pick quality)
     ✂️   Download a clip            (set time range)
     🎵  Download audio only        (full · best MP3)
     🎙️   Download audio clip        (set time range · MP3)
     📦  Download multiple videos   (parallel · auto best quality)
     🎶  Download multiple as audio (parallel · MP3)
     🔄  Update yt-dlp to latest version
     ❌  Quit
```

### 4. Pick your quality (for single video downloads)

```
  1    1080p   60fps  [mp4]  ~120 MB
  2    720p    30fps  [mp4]  ~60 MB
  3    480p    30fps  [mp4]  ~30 MB
  4    360p    30fps  [mp4]  ~15 MB
  A    Audio only (best quality, MP3)
```

> 💡 **Not sure which to pick?**
> - **1080p** = sharpest picture, largest file
> - **720p** = great quality, half the size (a good choice for most)
> - **480p / 360p** = smaller files, lower quality
> - **Audio only** = sound only, no video (smallest file)

### 5. Watch the progress bar

A clean progress bar shows how much is done:

```
  ⠸  Rick Astley - Never Gonna…  ████████████░░░░░░░  62.4%  3.1 MB/s  0:00:12
```

When it says **✓ Done!** your file is ready.

---

## 📂 Where do my files go?

All downloads are saved to:
```
~/Downloads/viddown/
```
That's the **viddown** folder inside your **Downloads** folder.

To save somewhere else, add `-o` followed by the path:
```
./dl "https://..." -o ~/Desktop/my-videos
```

---

## ✂️ Download only part of a video (a clip)

1. Run the tool: `./dl "https://..."`
2. Select **"Download a clip"** or **"Download audio clip"**
3. Enter the start and end times when asked

**Time formats you can use:**

| What you type | What it means |
|---|---|
| `5:23` | 5 minutes 23 seconds |
| `1:05:30` | 1 hour, 5 minutes, 30 seconds |
| `5m23s` | 5 minutes and 23 seconds |
| `323` | 323 seconds (= 5 min 23 sec) |

**Example — download 1 minute 21 seconds of a video:**
```
  Start time: 5:23
  End   time: 6:44
```

---

## 📦 Download multiple videos at the same time (parallel)

You can download several videos simultaneously — they all run in parallel and each  
shows its own progress bar.

1. Run: `./dl`
2. Select **"Download multiple videos"** or **"Download multiple as audio"**
3. Paste URLs one by one — press **Enter** after each one
4. When finished, press **Enter** on a blank line to start all downloads

```
  Enter video URLs
  One URL per line. Press Enter on a blank line when done.

  URL #1: https://www.youtube.com/watch?v=...
  URL #2: https://www.tiktok.com/@user/video/...
  URL #3: https://www.instagram.com/reel/...
  URL #4: [blank — press Enter to start]
```

All downloads then run **at the same time**, each with its own progress bar:

```
  ⠸  #1 youtube.com/watch?v=…    ████████░░░░░░░  54.2%  5.1 MB/s  0:00:08
  ⠼  #2 tiktok.com/@user/…       ██████████████░  88.7%  2.3 MB/s  0:00:02
  ⠋  #3 instagram.com/reel/…     ██░░░░░░░░░░░░░  14.1%  4.8 MB/s  0:00:21
```

> ℹ️ Parallel downloads use the best available quality automatically.  
> For custom quality per video, download them one at a time.

---

## ⏸️ Resuming downloads (internet dropped or download was paused)

**You never need to start over.** viddown automatically:

- **Retries failed fragments** — if a small piece of the file fails to download, it tries again immediately
- **Resumes from where it stopped** — if you close the tool or lose internet, just run the same download again and it picks up exactly where it left off, not re-downloading what it already has
- **Waits and retries** — if your internet is unstable, it retries up to 10 times with a 5-second wait between attempts

Simply re-run the exact same command and it will continue:
```
./dl "https://www.youtube.com/watch?v=..."
```
Pick the same quality as before and it resumes. ✅

---

## 🔄 Update the downloader

Sites change frequently. If something stops working:

1. Open the tool: `./dl "https://any-link"`
2. Select **"Update yt-dlp"**
3. Wait for it to finish, then try again

Or from the command line:
```
viddown --update
```

---

## 🖥️ Make `viddown` work from anywhere (Linux/Mac only)

So you can type `viddown` from any folder without navigating to the viddown directory:

```bash
chmod +x ~/Desktop/viddown/dl
mkdir -p ~/.local/bin
ln -sf ~/Desktop/viddown/dl ~/.local/bin/viddown
```

Then from any terminal:
```
viddown "https://..."
```

---

## ⌨️ Quick command reference

```bash
viddown                           # Open interactive menu (asks for URL)
viddown "https://..."             # Open menu with URL ready
viddown "https://..." --audio-only  # Download audio only (no menu)
viddown "https://..." --start 5:23 --end 6:44  # Download a clip (no menu)
viddown --build                   # Build or rebuild the Docker image
viddown --update                  # Update yt-dlp to latest version
```

---

## ❓ Frequently asked questions

**Q: The tool says "Docker image not found".**  
A: Run `./dl --build` to build it. This only needs to happen once.

**Q: The download failed or showed an error.**  
A: Select **"Update yt-dlp"** from the menu and try again. Some videos may be private,  
age-restricted, or region-locked — those cannot be downloaded.

**Q: Where is Docker? I don't see it.**  
A: Docker runs silently in the background. As long as Docker Desktop shows ✅ in your  
taskbar / menu bar, it's running.

**Q: I lost internet halfway through a large download. Do I need to start over?**  
A: No! Just run the same command again with the same quality. It will resume automatically.

**Q: Parallel downloads — how many videos can I download at once?**  
A: There's no hard limit, but your internet connection and computer speed determine what's  
practical. 3–5 videos at once is usually fine.

**Q: Can I download a whole YouTube playlist?**  
A: Not currently — this tool downloads one (or a few selected) videos at a time.

**Q: Something went wrong and I can't figure it out.**  
A: Email **rashidyaseen5484@gmail.com** with a description of what happened and  
copy-paste the error message. I'll help you out personally.

---

## 🌍 Supported sites

viddown supports **800+ websites** including:

YouTube · Instagram · TikTok · Twitter / X · Reddit · Facebook · Vimeo · Twitch · and [many more](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)

---

## 🤝 Get in touch

I built this because I wanted something simple that actually works for everyone.
If you run into a problem, have a suggestion, or just want to say hi —
drop me an email at **rashidyaseen5484@gmail.com**.

Made with ❤️ by **RY9**
