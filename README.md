# viddown — Video Downloader by RY9

Download videos from **YouTube, Instagram, TikTok**, and 800+ other sites.

You run it in a terminal (a text window), paste a link, pick what you want, and the file lands on your computer.

Works on **Windows, Mac, and Linux**. You do **not** need to know how to code.

---

## What can it do?

- Download a **full video** and pick the quality (1080p, 720p, …)
- Download only a **clip** (for example 5:23 to 6:44)
- Download **audio only** as MP3
- Download an **audio clip** as MP3
- Download **several videos at once**
- **Resume** if the internet drops — run the same download again
- **Update** itself when sites change

---

## What you need

**Only Docker.** Python, ffmpeg, and yt-dlp are included inside Docker.

| Computer | Download |
|---|---|
| Windows | [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) |
| Mac | [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) |
| Linux | [Docker Engine install guide](https://docs.docker.com/engine/install/) |

**Windows notes:**

1. Install Docker Desktop and **restart the computer** if it asks.
2. If it asks to install **WSL**, say yes.
3. After install, **open Docker Desktop** and leave it running. Wait until it says it is running (whale icon in the taskbar).
4. You never need to click around inside Docker. Just keep it open.

If Docker is not running, viddown cannot start.

---

## 1. Download the zip

1. Open the project page on GitHub.
2. Click the green **Code** button.
3. Click **Download ZIP**.
4. Save the zip somewhere easy, like your **Desktop** or **Downloads** folder.

---

## 2. Unzip the folder

**Windows**

1. Right-click the zip file.
2. Click **Extract All…**
3. Click **Extract**.
4. You should get a folder named `viddown` or `viddown-main`. Put it on your Desktop if you like.

**Mac**

Double-click the zip. A folder appears next to it.

**Linux**

Right-click → Extract, or:

```
unzip viddown-main.zip
```

Keep that folder. Do not delete it after setup — the global `viddown` command still points at it.

---

## 3. Open a terminal in that folder

A terminal is a text window where you type commands, then press **Enter**.

**Windows**

1. Press the **Start** button.
2. Type **PowerShell** and open **Windows PowerShell** (or **Windows Terminal**).
3. Go into the unzipped folder. Replace `YourName` with your Windows user name:

```
cd C:\Users\YourName\Desktop\viddown-main
```

If the folder is just called `viddown`:

```
cd C:\Users\YourName\Desktop\viddown
```

**Tip:** In File Explorer, click into the viddown folder, click the address bar, copy the path, then in PowerShell type `cd ` (with a space) and paste.

**Mac**

1. Press **Command + Space**, type **Terminal**, press Enter.
2. Type:

```
cd ~/Desktop/viddown-main
```

**Linux**

Press **Ctrl + Alt + T**, then:

```
cd ~/Desktop/viddown-main
```

**Tip (all systems):** you can drag the viddown folder onto the terminal window after typing `cd ` — it often pastes the path for you.

---

## 4. First-time setup (once)

This builds the tool (~5 minutes the first time) and makes the command `viddown` work from **any folder**.

### Windows

Make sure Docker Desktop is running, then:

```
.\viddown.ps1 --build
.\viddown.ps1 --install
```

If PowerShell says scripts are disabled, use **Command Prompt** instead (Start → `cmd`):

```
cd C:\Users\YourName\Desktop\viddown-main
viddown --build
viddown --install
```

Wait until you see **Image built successfully.**

Then **close the terminal completely and open a new one.**  
The `viddown` command only appears in new windows after `--install`.

### Mac / Linux

```
chmod +x dl
./dl --build
./dl --install
```

If `viddown` is not found after install, add this line to `~/.bashrc` or `~/.zshrc`, then open a new terminal:

```
export PATH="$HOME/.local/bin:$PATH"
```

---

## 5. Download a video (everyday use)

1. Copy a real video link from your browser (YouTube, Instagram, TikTok, …).
2. Open a **new** terminal. You can be in any folder — Downloads, Desktop, a project folder, etc.
3. Run (keep the quote marks):

```
viddown "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

4. Use **arrow keys** to move, **Enter** to select, **Q** to quit.

The file is saved in **the folder you are in right now**.  
Example: if your terminal shows `C:\Users\You\Desktop`, the video appears on the Desktop.

### Menu

```
  viddown  —  What would you like to do?

  >  Download full video        (pick quality)
     Download a clip            (set time range)
     Download audio only        (full · best MP3)
     Download audio clip        (set time range · MP3)
     Download multiple videos   (parallel · auto best quality)
     Download multiple as audio (parallel · MP3)
     Change save folder         (specify download location)
     Update yt-dlp to latest version
     Quit
```

### Quality picker

For a single video you can choose the resolution. Rough guide:

- **1080p** — sharpest, largest file
- **720p** — great for most people
- **480p / 360p** — smaller file, lower quality
- **Audio only** — MP3, no picture

---

## Where do the files go?

**Default:** the current folder (wherever your terminal is when you run `viddown`).

Save somewhere else with `-o`:

**Windows**

```
viddown "https://www.youtube.com/watch?v=dQw4w9WgXcQ" -o "D:\Videos"
```

**Mac / Linux**

```
viddown "https://www.youtube.com/watch?v=dQw4w9WgXcQ" -o ~/Desktop/my-videos
```

You can also pick **Change save folder** in the menu.

---

## Clips (only part of a video)

1. Run `viddown "YOUR-LINK"`
2. Choose **Download a clip** or **Download audio clip**
3. Type a start time and an end time

| You type | Meaning |
|---|---|
| `5:23` | 5 minutes 23 seconds |
| `1:05:30` | 1 hour, 5 minutes, 30 seconds |
| `5m23s` | 5 minutes and 23 seconds |
| `323` | 323 seconds |

Example: start `5:23`, end `6:44`.

Without the menu:

```
viddown "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --start 5:23 --end 6:44
```

---

## Several videos at once

1. Run `viddown` (no link)
2. Choose **Download multiple videos** or **Download multiple as audio**
3. Paste one URL, press Enter, repeat
4. Press Enter on an empty line to start

They download together. Quality is auto “best”. For a specific quality, download one at a time.

---

## Useful commands

After `--install`, these work from **any folder**. Always put links in `"quotes"`.

```
viddown
```
Open the menu, then paste a URL when asked.

```
viddown "https://..."
```
Open the menu with that URL already filled in.

```
viddown "https://..." --audio-only
```
Download the best MP3, no menu.

```
viddown "https://..." --start 5:23 --end 6:44
```
Download that clip as video, no menu.

```
viddown "https://..." -o "C:\Users\YourName\Videos"
```
Save into a specific folder (Windows example).

```
viddown --update
```
Update the downloader when a site stops working. Same as **Update yt-dlp** in the menu.

```
viddown --build
```
Rebuild the Docker image (first time, or after you change the code).

```
viddown --help
```
Show help.

```
viddown --install
```
Make `viddown` work from any folder (run this from the unzipped project folder).

```
viddown --uninstall
```
Remove the global command. The unzipped folder is not deleted.

---

## If the internet drops

You do not need to start over.

- Failed pieces are retried automatically
- If you close the window or lose Wi‑Fi, run the **same command** again and pick the **same quality**
- It continues from where it stopped (up to 10 retries, 5 seconds apart)

---

## If a download fails

1. Make sure Docker Desktop is still running.
2. Run `viddown --update` and try again.
3. Some videos cannot be downloaded: private, age-restricted, paid, or blocked in your country.

---

## Uninstall

Remove the global command:

```
viddown --uninstall
```

Optional extras:

- Delete the unzipped `viddown` folder
- In Docker Desktop, delete the image named `viddown` if you want it gone completely

---

## Common problems

**`viddown` is not recognized**  
You skipped `--install`, or you did not open a **new** terminal after install. From the unzipped folder run `.\viddown.ps1 --install` (Windows) or `./dl --install` (Mac/Linux), then open a new window.

**`.\viddown.ps1` is blocked on Windows**  
Use Command Prompt and `viddown --install` from the project folder, or run this once in PowerShell:

```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Docker image not found**  
Run `viddown --build` once. Docker Desktop must be running.

**I don’t see Docker**  
Look for the whale icon in the Windows taskbar or the Mac menu bar. Open **Docker Desktop** and wait until it is ready.

**Nothing happens / it asks for clip times on a fake link**  
Use a real URL from the browser. Do not use placeholders like `https://www.youtube.com/watch?v=...`  
Paste **one command at a time**.

**Where is my file?**  
In the folder your terminal was in. On Windows, `cd` to Desktop first if you want it on the Desktop:

```
cd $env:USERPROFILE\Desktop
viddown "https://..."
```

**PowerShell shows `>>` and waits**  
A quote was left open. Press **Ctrl+C** and type the command again with matching `"quotes"`.

**Can I download a whole YouTube playlist?**  
Not yet. One video, or a few URLs in parallel.

**I moved / renamed the viddown folder after --install**  
The global command still points at the old path. Run `--uninstall`, then `--install` again from the new folder.

---

## Optional: run without Docker

Only if you already use Python. Most people can ignore this.

You need **Python 3.10+**, **ffmpeg** on PATH, then:

```
pip install -r requirements.txt
python downloader.py
```

On Windows, that install includes `windows-curses` so the arrow-key menu works.  
Get ffmpeg from [ffmpeg.org/download.html](https://ffmpeg.org/download.html).

---

## Supported sites

YouTube · Instagram · TikTok · Twitter / X · Reddit · Facebook · Vimeo · Twitch · and [many more](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)

---

## Help

Something broke? Email **rashidyaseen5484@gmail.com** with:

- Your operating system (Windows / Mac / Linux)
- The exact command you ran
- Copy-paste of the error

Made with care by **RY9**
