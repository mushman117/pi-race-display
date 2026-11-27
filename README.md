# Pi Race Display

Live Formula 1 timing on a Raspberry Pi and the official 7" touchscreen. It sits next to your TV, shows the running order during every session, and takes over the screen for overtakes, penalties and Safety Cars. Between sessions it rotates a countdown, standings, results and a daily moment from racing history.

Runs on a Raspberry Pi 2 with 1 GB of RAM, so any later model works too.

![Pi Race Display on the official 7" screen](docs/photo.jpg)

## Features

**Live sessions**
- Top 5, 10, 15 or 20 in team colours with interval, gap to leader, current tyre and places gained since the start
- Practice and qualifying show best laps and gaps, with Q1, Q2 and Q3 and time remaining
- Status line for the closest battle, track status and race control messages
- Stream delay of 0 to 120 seconds so the screen matches a delayed TV stream

**Full-screen alerts**
- Overtakes in the top 10, with pit stops filtered out
- Battles: two top-10 cars within 1 second for two consecutive laps
- Safety Car, Virtual Safety Car, red flag, green flag and chequered flag
- Penalties, investigations, incidents noted, reprimands and disqualifications from race control

**Between sessions**
- Countdown to the next session in your time zone, with the track forecast
- Next track: round, race day, races held, laps and last year's winner and fastest lap
- Driver and team standings, with movement since the previous round
- Last race result, with places gained from the grid
- On this day: a win, first win or driver birthday from Formula 1 history, new every day

Tap the screen to skip to the next screen or dismiss an alert. Hold anywhere for 3 seconds to open the system menu: brightness, Reboot and Shut down (shutting down asks for a second tap to confirm). Its Settings page has the display mode, the sleep toggle and times, and auto-dim.

**Power and schedule**
- Brightness from 10% to 100%, set from the system menu and remembered across reboots
- Auto-dim between sessions to 50% of the brightness setting; tap for full brightness for 2 minutes
- Sleep schedule: the backlight turns off overnight (23:00 to 07:00 by default, adjustable in 30-minute steps from the menu); tap to wake for 2 minutes
- Display mode: always on, or only during race weekends (24 hours before first practice until 4 hours after the race start)
- Live sessions wake the screen, even overnight or outside race weekends

## Hardware

| Part | Tested with | Notes |
|---|---|---|
| Board | Raspberry Pi 2 | Pi 3, 4 and Zero 2 W should work unchanged |
| Screen | Official Raspberry Pi 7" touchscreen (800x480, DSI) | Other 800x480 framebuffer screens should work; touch needs an evdev device |
| WiFi | TP-Link USB dongle (Atheros AR5523) | Pi 2 has no built-in WiFi; Ethernet also works |
| Power | 5.1 V 2.5 A supply, short thick cable | A weak supply causes undervoltage, SD card corruption and WiFi dropouts |

Choose a WiFi dongle with an in-kernel driver. Chipsets that rely on staging drivers, such as the Realtek RTL8188SU (r8712u), have been removed from recent kernels.

## Install

### 1. Prepare the SD card

In Raspberry Pi Imager choose **Raspberry Pi OS Lite (32-bit)**. In the settings, set a hostname, username and password, your WiFi network and country, and enable SSH.

### 2. Connect the screen

Connect the ribbon cable to the Pi's DSI port. Power the screen from its own micro USB port, or from the Pi's GPIO (5 V on pin 2, ground on pin 6) if your supply can handle both.

### 3. Install the software

```bash
ssh <user>@<hostname>.local
sudo apt-get install -y git
git clone https://github.com/GITHUB_USERNAME/pi-race-display.git
cd pi-race-display
./install.sh
sudo reboot
```

The installer adds the system packages, puts your user in the `video`, `render` and `input` groups, allows your user to run only `systemctl reboot` and `systemctl poweroff` without a password (for the system menu), adds a udev rule so the app can set the backlight, and installs a systemd service that starts on boot.

After the reboot you see the starting lights, then the screens. On first run the app downloads Formula 1 race history in the background, which takes about 10 minutes because of the API's rate limit.

### 4. Test with the demo race

```bash
sudo systemctl stop racedisplay
python3 -m racedisplay --demo
```

A simulated 20-lap race runs overtakes, battles, a Safety Car and the chequered flag. Press Ctrl+C to quit, then `sudo systemctl start racedisplay`.

## Updating

```bash
cd ~/pi-race-display
git pull
sudo systemctl restart racedisplay
```

Run `./install.sh` again if a release notes a change to packages or the service file.

## Configuration

Edit `config.toml`, then run `sudo systemctl restart racedisplay`. Settings in the `[display]`, `[live]` and `[general]` sections apply as shown; the `[power]` section sets starting values, and later changes made in the system menu are saved in `~/.cache/racedisplay/settings.json`.

| Setting | Values | Purpose |
|---|---|---|
| `output` | `fb`, `sdl` | `fb` writes straight to `/dev/fb0` (default); `sdl` uses SDL's kmsdrm driver |
| `rotate` | `0`, `180` | Flips the picture if the screen is mounted upside down |
| `touch_rotate` | `0`, `180` | Optional; set only if menu buttons respond in the wrong place. Defaults to `rotate` |
| `rotate_seconds` | 5 or more | Seconds each screen shows between sessions |
| `places` | `5`, `10`, `15`, `20` | Drivers shown in live timing |
| `fps` | 1 to 10 | Redraw rate; 5 suits a Pi 2 |
| `screens` | list | Which between-session screens show, in order |
| `mode` | `always`, `race_weekends` | When the screen is on; the system menu overrides this |
| `brightness` | 10 to 100 | Starting backlight level; the system menu overrides this |
| `sleep` | `true`, `false` | Overnight screen-off on or off |
| `sleep_start`, `sleep_end` | `HH:MM` | Overnight screen-off period |
| `auto_dim` | `true`, `false` | Dim when no session is live |
| `dim_percent` | 10 to 100 | Dimmed level as a percentage of `brightness` |
| `wake_for_sessions` | `true`, `false` | Turn the screen on for live sessions during sleep or outside race weekends |
| `enabled` | `true`, `false` | Turns live timing on or off |
| `stream_delay` | 0 to 120 | Seconds to hold live data back to match your stream |
| `timezone` | e.g. `Europe/London` | Blank uses the Pi's time zone (`sudo raspi-config`) |

## How it works

The app is a single Python process with three background threads and a 5 fps drawing loop.

- **Schedule and statistics** (`data.py`) poll the Jolpica API every 30 minutes for the calendar, standings, last result and next-track facts, and Open-Meteo for the forecast. Results are cached in `~/.cache/racedisplay`, so the screens work after a reboot without a connection.
- **Live timing** (`livetiming.py`) connects to F1's SignalR Core feed from 10 minutes before each session. When F1 reports the session finished, the final order stays up for 15 minutes (tap to skip), then the screens rotate again. If no finish is reported, it stops 20 minutes after the scheduled end. Updates are merged into one state tree and held in a queue for the configured stream delay.
- **Race logic** (`race.py`) turns that state into table rows and detects overtakes, battles, flag changes and race control events.
- **Screens** (`screens.py`, `ui.py`) draw with pygame on an off-screen surface.
- **Output** (`output.py`) writes each frame to the framebuffer, converting to 16-bit colour where needed, and reads taps from the touchscreen through evdev.

Drawing to the framebuffer avoids the GPU and display-server stack, which keeps memory and CPU use low on older Pis.

## Project structure

```
racedisplay/
  main.py         Main loop, configuration, screen selection
  data.py         Jolpica and Open-Meteo clients, cache, on-this-day
  livetiming.py   F1 SignalR Core client and demo race
  race.py         Live table rows, alerts and status line
  screens.py      Every screen layout
  ui.py           Fonts, colours and drawing helpers
  output.py       Framebuffer output and touch input
config.toml       Settings
install.sh        Package install and systemd setup
racedisplay.service
screen-setup.sh   Disables console blanking and the cursor
```

## Data sources and limits

| Data | Source | Notes |
|---|---|---|
| Live timing | F1's SignalR Core feed at livetiming.formula1.com | Unofficial and undocumented |
| Calendar, standings, results, history | [Jolpica F1 API](https://github.com/jolpica/jolpica-f1) | Successor to the Ergast API; rate limited |
| Weather | [Open-Meteo](https://open-meteo.com) | Forecast available within 15 days of a session |

F1 changes its live timing service without notice. In October 2026 it retired the older SignalR endpoint and put its session archive behind an F1 TV login. The SignalR Core feed this project uses worked without a login at the time of writing. If F1 restricts it, the log shows `F1 refused access (401)` and every screen except live timing keeps working.

Live timing data is for personal use. Do not use this project to redistribute F1 data.

Not included: track map graphics (the free APIs have no circuit outlines) and phone notifications.

## Troubleshooting

View the log with `journalctl -u racedisplay -f`.

| Symptom | Cause and fix |
|---|---|
| `pygame.error: EGL not initialized` | Only affects `output = "sdl"`. Run `./install.sh` again to add the Mesa libraries, or switch back to `fb` |
| Black screen while the service runs | Stop the service and run `sudo sh -c 'cat /dev/urandom > /dev/fb0'`. Coloured noise means the display works and the log shows the fault; still black points to the ribbon cable, display power or undervoltage |
| Picture upside down | Set `rotate = 180` (or `0`) in `config.toml` |
| Screen goes blank after 10 minutes | Console blanking; check `screen-setup.sh` ran (the log shows an error if not) |
| Lightning bolt or undervoltage warnings | Run `vcgencmd get_throttled`; anything other than `0x0` means the supply or cable is too weak |
| `429 Too Many Requests` from Jolpica | Rate limit during the history download; the app waits and retries |
| Brightness buttons do nothing | Log shows `backlight not writable`; run `./install.sh` again and reboot |
| Menu shows "Not permitted" | The sudoers rule is missing; run `./install.sh` again |
| `F1 refused access (401)` | F1 has restricted the live feed; see Data sources and limits |
| "Waiting for live timing" during a session | Feed unreachable; check WiFi with `nmcli device` and the log |
| Wrong session times | Set the Pi's time zone or `timezone` in `config.toml` |

## Disclaimer

This is an unofficial project. It is not associated with Formula 1 companies. F1, FORMULA ONE, FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP, GRAND PRIX and related marks are trademarks of Formula One Licensing B.V.

## Licence

[MIT](LICENSE)
