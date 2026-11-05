# Sarah Sambo Cloud Operations Lab

A local Python application for generating virtual cloud jobs and predicting resource-related task failures. The simulator generates tasks; the Random Forest model predicts whether an active task will fail in the next five simulated minutes. No real cloud account, uploaded files, or incoming real users are required.

## Start the app

**Offline transfer:** use the complete `Sarah_Cloud_Lab_OFFLINE_Windows_x64.zip` portable build. Extract All, then double-click START_APP.bat. That edition includes Python and all libraries and needs no installation or Internet. A GitHub source ZIP is not the portable edition. See [offline build and transfer instructions](docs/OFFLINE_DISTRIBUTION.md).

**Source checkout:** follow the setup below once before running.

Install **Python 3.14**, clone or download this repository, then double-click **SETUP.bat** once with Internet access. Setup creates an isolated `.venv` and installs the tested package versions from `requirements-lock.txt`.

Double-click **START_APP.bat** to open **http://127.0.0.1:5057**. Keep its terminal window open while using the lab. Close that window or press Ctrl+C there to stop the server. Once installed, normal operation works offline.

The selected trained Random Forest and its reproducible dataset release are included under `models/`. Training from scratch is optional. Generated user experiments are stored locally in `data/cloudlab.sqlite3`, which is excluded from Git.

The `docs/` directory contains Sarah's original Chapters 1–3. The implemented retry queue extends that document's advisory-only scope; see METHODOLOGY.md for the distinction between first-attempt prediction and recovery.

## Your first experiment

1. Leave **Normal activity** selected, or choose **Busy activity** to see more competition for resources.
2. Select **Start simulation**. The app automatically creates virtual jobs and assigns them to servers.
3. Watch the job table. CPU is computing power; memory is working space. Progress counts minutes in which a job received its required resources.
4. Read any warning. It estimates failure within five simulated minutes; it is not a promise. A warned task may complete successfully.
5. Use **Pause** and **Advance 1 minute** to inspect events. Select a server to see only its jobs.
6. Open the optional replay/report section to inspect saved minutes or download CSV and JSON results.

The model is fixed for the life of each experiment. Training a new model affects new experiments only. Without a model, the simulator still runs and clearly shows monitoring-only mode.

## Task behaviour

Tasks have CPU and memory requests, an arrival time, an assigned server, and a required processing duration. Names such as Report generation and Image processing are illustrative: the application does not process actual reports or images.

Tasks receive resources using FIFO ordering with backfilling. A task gets all its requested CPU and memory for a tick or gets neither. A later smaller task can run if it fits. Three consecutive resource-shortfall ticks cause failure. A successful allocation resets that streak. One tick equals one simulated minute, regardless of playback speed.

## Warnings and outcomes

- **Outcome pending:** the five-minute warning window has not been fully observed.
- **Confirmed warning:** the task failed within five minutes of at least one warning in the episode.
- **False alarm:** no failure occurred in the episode's warning windows, including a task that completed after warning.
- **Follow-up incomplete:** the experiment ended before an outcome could be established. It is not counted as a successful prediction.

Repeated consecutive warnings for one task form one episode. Mark as reviewed records acknowledgement; it does not repair the task or erase the prediction. ML warnings are advisory. An optional background retry queue reacts to actual initial-attempt failures, independently of predictions. It never takes priority over incoming first attempts.

## Model preparation and evaluation

Use **Model & results → Generate data & train model**, or double-click **TRAIN_MODEL.bat**. The default experiment generates 600 independent runs: 200 for each workload. Whole runs are divided into 360 training, 120 validation, and 120 final-test runs. Thirty additional capacity-stress runs are evaluated separately. CPU-based preparation may take several minutes.

The results page reports accuracy, precision, recall, F1, confusion counts, false alert episodes, task-level detection and warning lead time. A learning curve compares validation results with increasing numbers of training runs. More synthetic rows alone cannot prove real-world predictive accuracy.

## Files

- `cloudlab/simulator.py`: seeded task arrivals, resource allocation, failure rules and future-window labels.
- `train.py`: synthetic dataset generation, model selection, evaluation and versioned model artifacts.
- `app.py`: local Flask service and APIs.
- `cloudlab/storage.py`: SQLite persistence.
- `static/` and `templates/`: offline interface, purple themes and supplied cloud image.
- `data/cloudlab.sqlite3`: user experiments and snapshots.
- `models/<version>/`: compressed datasets, run settings/seeds, model, measured metrics and test predictions.
- `models/current.json`: currently selected model version.

## Verification

Run `.venv\Scripts\python.exe -m pytest --basetemp work\pytest-new-run` using a fresh temporary directory name. Tests cover reproducibility, resource conservation, failure rules, future-window labels, false alarms, persistence, exports, replay and local-only access. JavaScript can be checked with `node --check static/app.js`.

## Limits

This implements Sarah's simulation-based scope. It does not predict hardware outages, cyberattacks, or all cloud failures, and it does not host user applications. Good results mean that the model learned the simulator's defined process. Real-cloud predictive validity remains unverified. The larger 600-run experiment is an implementation change from the chapters' initially planned 150 runs and should be reflected in the final methodology/results.

## Automatic recovery queue

New browser experiments enable retries by default. Turn them off under optional experiment settings to reproduce the original prediction-only behaviour. Training and benchmark generation keep retries disabled, so the saved model metrics still describe the original first-attempt process.

After an actual failure, a job waits at least three simulated minutes. It can restart on the quietest suitable server when both CPU and memory utilisation are at most 65% before admission and its full requests fit. One new retry per server per tick is allowed. First-attempt jobs always receive resources first. Retry allocations use only the remaining capacity and are shown separately internally; the server display shows total usage.

A retry restarts the job from zero. If it suffers three consecutive allocation shortfalls, it queues again with backoff. At most two retries are admitted. A second failed retry reaches the limit. A job which never gets a slot remains queued rather than consuming a retry.

When the configured arrival period ends, no new jobs are generated. Existing initial jobs and background retries continue for at most 120 additional simulated minutes. The clock labels this Finishing jobs. The run stops early if no work remains. Jobs still waiting or running at the limit are unfinished, not permanent failures or successes.

The task table and recovery queue distinguish queued, retrying, recovered and retry-limit outcomes. The main completed count includes successful recoveries, and permanent failures are separate. Original failure_tick and status are retained for ML labels; retry status, attempt history, retry server and recovered_tick are additional fields in SQLite and JSON reports. CSV prediction exports continue to represent initial-attempt windows. A correct warning stays correct after recovery. No predictions are made for retry attempts.

The recovery capability extends the chapters' original advisory-only prototype. The original chapter file has not been rewritten. It is not evidence that ML itself prevented a failure.

## Included measured model release

The selected model uses 600 independent simulated runs and 1,089,871 labelled observations. Whole-run partitions contain 360 training, 120 validation and 120 test runs. Held-out observation-level accuracy is 97.58%, precision 82.70%, recall 98.27%, and F1 89.81%; median eligible warning lead time is two simulated minutes. These synthetic results do not establish accuracy on real cloud infrastructure. The included `metrics.json` reports false alarms, missed failures, class counts, reference comparisons and separate stress results.

Validation completed with 33 passing Python tests and a JavaScript syntax check. The tests audit independent data partitions, label timing, serialized predictions, recovery limits and preservation of original failure outcomes. The browser interface was checked with the final model and recovery queue.
