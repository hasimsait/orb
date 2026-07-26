# Orb — Desktop Pet powered by mpv + LLM

A floating animated orb that lives on your desktop. It reacts to your mouse (hover/leave) and opens an LLM chat terminal when clicked.

![idle](idle.gif) → ![hover](hover.gif) → ![output](output.gif)

## How It Works

| Event | What Happens | Script |
|---|---|---|
| **Idle** | Plays `idle.gif` in a borderless mpv window | — |
| **Mouse Enter** | Swaps to `hover.gif` | `orb_hover.sh` |
| **Mouse Leave** | Swaps back to `idle.gif` | `orb_leave.sh` |
| **Left Click** | Plays `output.gif`, opens Alacritty with LLM chat, then returns to `idle.gif` | `orb_action.sh` |

All GIF swaps happen over IPC by sending `loadfile` commands to mpv's Unix socket at `/tmp/orb-socket`.

---

## Prerequisites

| Dependency | Purpose | Install (Debian/Ubuntu) |
|---|---|---|
| [mpv](https://mpv.io/) | Renders the animated GIF overlay | `sudo apt install mpv` |
| [socat](http://www.dest-unreach.org/socat/) | Sends IPC commands to mpv's Unix socket | `sudo apt install socat` |
| [Alacritty](https://alacritty.org/) | Terminal emulator for the chat UI | `sudo apt install alacritty` |
| Python 3 | Runs the chat client | (usually pre-installed) |
| A local LLM server | OpenAI-compatible API on `localhost:8080` | See [LLM Backend](#llm-backend) |

---

## Installation

### 1. Clone / Copy files

Clone or copy the project to any directory. `~/.config/orb` is the conventional location, but any path works — all scripts resolve paths relative to themselves.

```bash
git clone <repo-url> ~/.config/orb
```

The directory should contain:

```
orb/
├── start_orb.sh        # Launcher (entry point)
├── orb_input.conf      # mpv input bindings (mouse events)
├── orb_hover.sh        # Script: switch to hover animation
├── orb_leave.sh        # Script: switch to idle animation
├── orb_action.sh       # Script: click handler (chat launch)
├── client.py         # LLM chat client (streaming, tool-use, leveled logging)
├── log.py              # Leveled logger — writes to ORB_LOG_FILE
├── system_prompt.txt   # System prompt for the LLM
├── idle.gif            # Animation: idle state
├── hover.gif           # Animation: mouse hover state
└── output.gif          # Animation: thinking/action state
```

### 2. Make scripts executable

```bash
chmod +x ~/.config/orb/*.sh
```

### 3. Launch the Orb

```bash
~/.config/orb/start_orb.sh
```

`start_orb.sh` exports `ORB_DIR` and launches mpv with all the right flags. All child scripts (`orb_hover.sh`, etc.) also self-resolve their directory, so nothing depends on a hardcoded install path.

> **Tip:** Edit `--geometry=128x128+50+50` inside `start_orb.sh` to change size (`WxH`) and position (`+X+Y`).

---

## LLM Backend

The chat client (`client.py`) expects an **OpenAI-compatible** API at:

```
http://localhost:8080/v1/chat/completions
```

Any server that implements this endpoint will work. Some options:

| Server | Command |
|---|---|
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | `llama-server -m model.gguf --port 8080` |
| [Ollama](https://ollama.com/) | Runs on port `11434` by default — update the URL in `client.py` |
| [vLLM](https://github.com/vllm-project/vllm) | `vllm serve model --port 8080` |
| [LM Studio](https://lmstudio.ai/) | Enable local server in settings |

The chat client supports:
- Streaming responses (SSE)
- Reasoning/thinking tokens (displayed dimmed)
- Tool calling (`web_search_exa`, `web_fetch_exa`)
- Structured logging to file (see [Debugging](#debugging))

### Customizing the Personality

Edit `system_prompt.txt` to change how the orb responds.

---

## Customization

### GIF Animations

Replace the GIF files to change the orb's appearance:

| File | When it plays |
|---|---|
| `idle.gif` | Default resting state |
| `hover.gif` | Mouse is hovering over the orb |
| `output.gif` | Orb is "thinking" (click action) |

### Window Size & Position

Edit the `--geometry` flag in your launch command:

```
--geometry=WIDTHxHEIGHT+X_OFFSET+Y_OFFSET
```

Example: `--geometry=200x200-20-20` → 200×200 pixel orb in the bottom-right corner.

### Terminal Emulator

The click handler uses **Alacritty** by default. To use a different terminal, edit [orb_action.sh](orb_action.sh) line 6:

```bash
# Replace 'alacritty --class OracleOutput -e' with your terminal's equivalent:
kitty -e python3 "$DIR/client.py" "$PROMPT"
```

### LLM Endpoint

Edit the `url` variable at the top of [client.py](client.py):

```python
url = "http://localhost:8080/v1/chat/completions"
```

---

## Debugging

The chat client logs to a file via `log.py`. Two environment variables control it:

| Variable | Default | Description |
|---|---|---|
| `ORB_LOG` | *(unset — errors only)* | Verbosity level: `error` \| `warn` \| `debug` \| `trace` |
| `ORB_LOG_FILE` | `/tmp/orb.log` | Path to the log file (created/appended) |

You can set them in `start_orb.sh` (they are already declared there), or export them before launching:

```bash
# Watch the log in a separate terminal
ORB_LOG=debug start_orb.sh
tail -f /tmp/orb.log
```

**Log levels:**

| Level | When it fires | Typical content |
|---|---|---|
| `error` | **Always** (also echoed to stderr) | Connection failures, bad tool names, parse errors |
| `warn` | `ORB_LOG=warn` or higher | Exa MCP fallback, unexpected empty replies |
| `debug` | `ORB_LOG=debug` or higher | Every significant step: session init, HTTP request, tool routing, message history mutations |
| `trace` | `ORB_LOG=trace` only | Raw SSE lines, full request body JSON, full tool results |

Log entry format:
```
02:40:15.412 [DEBUG] Session initialized: system_prompt=1024 chars, initial_prompt=none
02:40:15.413 [DEBUG] → POST http://localhost:8080/v1/chat/completions  (history=1 messages)
02:40:22.118 [ WARN] Exa MCP failed: connection refused — falling back to local handlers
02:40:22.119 [ERROR] Wikipedia fallback request failed: timeout
```

---

## Autostart (optional)

To launch the orb on login, add it to your desktop environment's autostart or create a systemd user service:

```bash
cat > ~/.config/systemd/user/orb.service << 'EOF'
[Unit]
Description=Desktop Orb Pet
After=graphical-session.target

[Service]
ExecStart=%h/.config/orb/start_orb.sh
Restart=on-failure
Environment=DISPLAY=:0

[Install]
WantedBy=graphical-session.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now orb.service
```

---

## Troubleshooting

### Hover/leave doesn't work

1. **Check the socket exists:**
   ```bash
   ls -la /tmp/orb-socket
   ```
   If missing, mpv wasn't started with `--input-ipc-server=/tmp/orb-socket`.

2. **Test the IPC manually:**
   ```bash
   echo '{ "command": ["loadfile", "'$HOME'/.config/orb/hover.gif"] }' | socat - /tmp/orb-socket
   ```
   If this works but mouse events don't, the issue is in `orb_input.conf` or script permissions.

3. **Check scripts are executable:**
   ```bash
   ls -la ~/.config/orb/orb_*.sh
   ```

4. **Check socat is installed:**
   ```bash
   which socat
   ```

### Chat doesn't open on click

- Verify Alacritty is installed: `which alacritty`
- Verify the LLM server is running: `curl http://localhost:8080/v1/models`
- Check the log for errors: `cat /tmp/orb.log` (or `tail -f /tmp/orb.log` while clicking)

### mpv window has borders or controls

Make sure you're passing `--no-border --no-osc` to mpv.
