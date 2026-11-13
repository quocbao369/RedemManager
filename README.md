# RedemManager

Standalone Python dashboard for managing LDPlayer 9 / 14 instances, ADB, app installation, proxy configuration, and automated workflows for Bing login.

## Features

- **LDPlayer Manager**: List, create, start, stop, and remove instances; check ADB connectivity and boot status; supports TWO installations (LDPlayer 9 and optionally LDPlayer 14) — pick the emulator type when creating a new instance
- **App Manager**: Verify APK/XAPK files, install Bing APK and OxyProxy XAPK, launch apps
- **Proxy Manager**: Add HTTP/HTTPS/SOCKS5 proxies with authentication, test connectivity, configure on device
- **Workflow Monitor**: 11-step automated workflow with real-time WebSocket updates
- **Logs**: Filterable live logs with JSON export
- **Settings**: Configure paths, timeouts, and server settings with validation; OS file picker for DB paths with instant load (no restart)

## Workflow Steps

1. **Preflight** - Verify LDPlayer, ADB, APKs, and device connectivity
2. **Create Instance** - Create new LDPlayer instance
3. **Start Instance** - Launch the instance
4. **Wait for Android Boot** - Wait for `sys.boot_completed=1`
5. **Install Bing** - Install Microsoft Bing APK
6. **Install OxyProxy** - Install OxyProxy XAPK (handles split APKs)
7. **Configure Proxy** - Set system proxy on Android device
8. **Verify Connection** - Test connectivity through proxy
9. **Launch Bing** - Open Bing app
10. **Wait for Manual Login** - Pause for user to complete Microsoft login
11. **Generate Report** - Summary of all steps

## Requirements

- Python 3.11+
- LDPlayer 9 installed (LDPlayer 14 optional — set its folder in Settings > Paths)
- ADB (bundled with LDPlayer)
- Windows 10/11

## Installation

1. Copy `.env.example` to `.env` and adjust paths:

   ```cmd
   copy .env.example .env
   notepad .env
   ```

2. Run the launcher:

   ```cmd
   run.bat
   ```

   This will:
   - Create virtual environment in `.venv/`
   - Install dependencies from `requirements.txt`
   - Start Uvicorn on `http://127.0.0.1:8765`

3. Open browser to `http://127.0.0.1:8765`

## Manual Setup

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8765
```

## Configuration

All settings in `.env`:

| Variable             | Description                  | Default                                                     |
| -------------------- | ---------------------------- | ----------------------------------------------------------- |
| `LDPLAYER_PATH`      | LDPlayer 9 install directory | `C:\ProgramFiles\LDPlayer`                                  |
| `LDPLAYER14_PATH`    | LDPlayer 14 install directory (optional; empty = disabled) | *(empty)*                     |
| `BING_APK_PATH`      | Path to Bing APK             | `D:\AccMicro\MicrosoftBing_33.5.440623004_apkcombo.com.apk` |
| `OXYPROXY_XAPK_PATH` | Path to OxyProxy XAPK        | `D:\AccMicro\OxyProxy.xapk`                                 |
| `SERVER_PORT`        | Dashboard port               | `8765`                                                      |
| `DEFAULT_TIMEOUT`    | Command timeout (s)          | `120`                                                       |
| `BOOT_TIMEOUT`       | Android boot timeout (s)     | `300`                                                       |

## Project Structure

```
RedemManager/
├── app.py              # FastAPI application
├── config.py           # Configuration management
├── database.py         # SQLite models and queries
├── workflow.py         # Workflow engine
├── requirements.txt    # Python dependencies
├── .env.example        # Environment template
├── .gitignore
├── README.md
├── run.bat             # Windows launcher
├── services/
│   ├── __init__.py
│   ├── ldplayer_service.py   # LDPlayer CLI wrapper
│   ├── adb_service.py        # ADB wrapper
│   ├── installer_service.py  # APK/XAPK installer
│   ├── proxy_service.py      # Proxy management
│   ├── bing_service.py       # Bing automation
│   └── logger_service.py     # Logging setup
├── templates/          # Jinja2 templates
├── static/
│   ├── css/style.css
│   └── js/*.js
├── data/               # SQLite database
├── logs/               # Application logs
└── tests/              # Unit tests
```

## Security

- Binds to `127.0.0.1` only (no LAN exposure)
- No arbitrary shell command execution
- Proxy passwords not logged (base64 encoded in DB)
- Input validation on all endpoints
- Confirmation required for destructive actions

## Testing

```cmd
.venv\Scripts\activate
pytest tests/ -v
```

## License

Internal use only.
