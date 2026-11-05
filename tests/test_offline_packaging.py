import hashlib
import json
from pathlib import Path
import zipfile
import pytest
from scripts.build_offline import application_files,write_manifest,verify_archive


def test_source_selection_excludes_runtime_history_and_old_models(tmp_path):
    (tmp_path/"models/current").mkdir(parents=True)
    (tmp_path/"models/old").mkdir()
    (tmp_path/"models/current.json").write_text('{"version":"current"}')
    (tmp_path/"models/current/model.joblib").write_bytes(b"model")
    (tmp_path/"models/current/selected_candidate.joblib").write_bytes(b"checkpoint")
    (tmp_path/"models/old/model.joblib").write_bytes(b"old")
    (tmp_path/"runtime").mkdir();(tmp_path/"runtime/python.exe").write_bytes(b"runtime")
    (tmp_path/"data").mkdir();(tmp_path/"data/cloudlab.sqlite3").write_bytes(b"private history")
    files={p.relative_to(tmp_path).as_posix() for p in application_files(tmp_path,"current")}
    assert files=={"models/current.json","models/current/model.joblib"}


def test_model_version_cannot_escape_model_directory(tmp_path):
    with pytest.raises(ValueError):application_files(tmp_path,"..")


def test_archive_integrity_covers_bundled_files(tmp_path):
    stage=tmp_path/"stage";stage.mkdir()
    (stage/"app.py").write_text("print('example')")
    write_manifest(stage)
    archive=tmp_path/"offline.zip"
    with zipfile.ZipFile(archive,"w") as z:
        for p in stage.iterdir():z.write(p,"Sarah Cloud Lab Offline/"+p.name)
    assert verify_archive(archive)==1
    broken=tmp_path/"broken.zip"
    with zipfile.ZipFile(broken,"w") as z:
        z.writestr("Sarah Cloud Lab Offline/app.py","different content")
        z.write(stage/"OFFLINE_MANIFEST.json","Sarah Cloud Lab Offline/OFFLINE_MANIFEST.json")
    with pytest.raises(ValueError,match="hash mismatch"):verify_archive(broken)
