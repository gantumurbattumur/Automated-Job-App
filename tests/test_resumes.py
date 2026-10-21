import pytest
import yaml

from backend import resume

TEXT = ("Software engineer and M.S. student. Python, PyTorch, LLM evaluation, FastAPI, PostgreSQL, Docker, AWS. "
        "Built retrieval-augmented generation systems and backend APIs used by hundreds of students. ") * 3


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, "DATA", tmp_path)
    monkeypatch.setattr(resume, "CONF", tmp_path / "resumes.yaml")
    monkeypatch.setattr(resume, "STORE", tmp_path / "resume")
    monkeypatch.setattr(resume, "ROOT", tmp_path)  # no old config to migrate
    monkeypatch.setattr(resume, "changed", lambda: None)
    return tmp_path


def test_add_replace_versions_revert(store):
    name = resume.add("AI / ML", "My Resume.txt", TEXT.encode(), ["machine learning"])
    assert name == "ai_ml" and resume.primary() == "ai_ml" and resume.targets() == {"ai_ml": ["machine learning"]}
    first = resume.path("ai_ml").name
    resume.replace("ai_ml", "v2.txt", (TEXT + " Kubernetes").encode())
    assert "Kubernetes" in resume.text("ai_ml")
    assert [v["file"] for v in resume.versions("ai_ml")] == [first]  # the old file is kept as a version
    resume.revert("ai_ml", first)
    assert "Kubernetes" not in resume.text("ai_ml")


def test_primary_label_targets_and_delete(store):
    resume.add("AI/ML", "a.txt", TEXT.encode())
    resume.add("Software", "b.txt", TEXT.encode())
    resume.update("software", label="SWE", targets_=["Backend", " "], make_primary=True)
    cfg = resume.config()
    assert cfg["primary"] == "software" and cfg["lanes"]["software"] == {**cfg["lanes"]["software"], "label": "SWE", "targets": ["backend"]}
    resume.delete("software")
    assert resume.names() == ["ai_ml"] and resume.primary() == "ai_ml"
    assert (store / "resume" / "_removed").exists()  # files are moved aside, not destroyed
    with pytest.raises(ValueError):
        resume.delete("ai_ml")  # keep at least one


@pytest.mark.parametrize("filename,data,msg", [
    ("resume.png", b"x" * 500, "PDF, DOCX or TXT"),
    ("resume.txt", b"too short", "scanned"),
    ("resume.txt", b"x" * (6 * 1024 * 1024), "5 MB"),
])
def test_rejects_bad_files(store, filename, data, msg):
    with pytest.raises(ValueError, match=msg):
        resume.add("AI", filename, data)
    assert not list((store / "resume").rglob("*.*")) if (store / "resume").exists() else True


def test_duplicate_names_and_path_tricks(store):
    resume.add("AI", "a.txt", TEXT.encode())
    with pytest.raises(ValueError, match="already have"):
        resume.add("ai", "b.txt", TEXT.encode())
    with pytest.raises(KeyError):
        resume.revert("ai", "../../resumes.yaml")


def test_migrates_old_config(store):
    (store / "config").mkdir()
    (store / "resume" / "X").mkdir(parents=True)
    (store / "resume" / "X" / "r.txt").write_text(TEXT)
    (store / "config" / "resumes.yaml").write_text(yaml.safe_dump(
        {"primary": "ai_ml", "lanes": {"ai_ml": str(store / "resume" / "X" / "r.txt")}, "targets": {"ai_ml": ["ml"]}}))
    cfg = resume.config()
    assert cfg["primary"] == "ai_ml" and cfg["lanes"]["ai_ml"]["file"] == "resume/X/r.txt"
    assert cfg["lanes"]["ai_ml"]["targets"] == ["ml"] and "PyTorch" in resume.text("ai_ml")
