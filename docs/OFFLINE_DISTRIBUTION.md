# Offline Windows distribution

The GitHub source ZIP does not include Python or installed dependencies. On a computer without Python, extracting that source ZIP and starting the app will require online setup. The portable offline ZIP includes the runtime and libraries and needs no installation or Internet connection on the receiving computer.

## Build the portable ZIP

Use 64-bit Windows with Python 3.14. Run SETUP.bat once to install the pinned requirements, then run this command from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\build_offline.py
```

The builder uses the selected model in models/current.json, this checkout's source, the active Python environment's installed packages, and its base Python runtime. It has no developer-specific paths and needs no pre-existing release ZIP. It checks the Python and dependency versions before building.

The output is dist/Sarah_Cloud_Lab_OFFLINE_Windows_x64.zip. A staging directory under work/ is retained for inspection. Both locations and the bundled runtime are ignored by Git. Do not commit the portable ZIP or a local .venv to the source repository. Distribute the ZIP as a GitHub Release asset or directly to the recipient.

## Run on Sarah's computer

1. Copy the complete offline ZIP to the 64-bit Windows computer.
2. Right-click it and choose Extract All.
3. Open the extracted Sarah Cloud Lab Offline folder.
4. Double-click START_APP.bat and keep the terminal open while using the app.
5. If the browser does not open, visit http://127.0.0.1:5057 manually.

SETUP.bat is unnecessary in an offline bundle. The shared launcher automatically uses runtime/python.exe when bundled, otherwise the source environment's .venv Python. Startup exceptions are retained in data/startup-error.log instead of disappearing with the terminal.

## Verification

The builder writes a SHA-256 manifest for all bundled files and verifies the compressed ZIP before returning. For a release, extract to a fresh folder and verify that the bundled Python uses only paths inside that folder. Run the tests with the bundled runtime and a temporary directory outside the release contents. Start on a separate local port so the check cannot accidentally reuse an already running lab.

The first portable release was tested with Python environment variables pointing to nonexistent paths and without installed Python on PATH. All 30 original tests passed with its bundled interpreter, and a fresh extraction loaded the model and advanced a simulation. This does not claim testing on every Windows hardware configuration.
