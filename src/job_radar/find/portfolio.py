"""Mode 1 — search from a portfolio.

A portfolio is evidence, not self-assessment: each skill gets a level from how
many projects show it (1 project -> 2 "used in projects", 3+ projects -> 3).
Inputs, any mix:
  - a local folder of projects (reads README*, *.md, requirements*.txt,
    pyproject.toml, package.json, environment.yml, *.ipynb markdown cells)
  - single files (.md .txt .pdf .docx .ipynb)
  - github:<username>  — public repos via the official GitHub REST API
    (name, description, topics, languages, README), unauthenticated, polite.
"""

from __future__ import annotations

import base64
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..extract.llm import LLMClient
from ..http import PoliteClient
from ..match.profile import Profile, profile_from_text, read_document
from ..skills import canonical, find_in_text

# Package names that imply a skill but are not spelled like it.
PACKAGE_SKILLS = {
    "torch": "pytorch", "torchvision": "pytorch", "lightning": "pytorch", "tensorflow": "tensorflow", "keras": "tensorflow",
    "sklearn": "scikit-learn", "scikit-learn": "scikit-learn", "xgboost": "xgboost", "lightgbm": "xgboost",
    "transformers": "hugging face", "datasets": "hugging face", "peft": "fine-tuning", "trl": "fine-tuning",
    "langchain": "langchain", "llama-index": "langchain", "llama_index": "langchain", "openai": "openai api",
    "anthropic": "openai api", "faiss-cpu": "vector databases", "faiss": "vector databases", "chromadb": "vector databases",
    "qdrant-client": "vector databases", "pinecone-client": "vector databases", "pgvector": "vector databases",
    "fastapi": "fastapi", "flask": "fastapi", "django": "fastapi", "pandas": "pandas", "polars": "pandas", "numpy": "numpy",
    "scipy": "numpy", "pyspark": "spark", "mlflow": "mlflow", "wandb": "mlflow", "opencv-python": "computer vision",
    "ultralytics": "computer vision", "spacy": "nlp", "nltk": "nlp", "sentence-transformers": "hugging face",
    "onnx": "onnx", "onnxruntime": "onnx", "vllm": "onnx", "pymongo": "mongodb", "psycopg2": "sql", "sqlalchemy": "sql",
    "boto3": "aws", "google-cloud-aiplatform": "gcp", "azure-ai-ml": "azure", "apache-airflow": "airflow", "prefect": "airflow",
    "streamlit": "fastapi", "docker": "docker", "kubernetes": "kubernetes", "jax": "jax", "flax": "jax",
}
GITHUB_LANG = {"Python": "python", "Jupyter Notebook": "python", "TypeScript": "typescript", "JavaScript": "typescript",
               "Go": "go", "Rust": "rust", "C++": "c++", "Java": "java", "Scala": "scala", "R": "r", "Shell": "bash",
               "HCL": "terraform", "Dockerfile": "docker", "SQL": "sql"}
_FILES = ("README*", "*.md", "requirements*.txt", "pyproject.toml", "package.json", "environment.yml", "Dockerfile",
          "docker-compose*.yml", "*.ipynb")


@dataclass
class Project:
    name: str
    text: str
    skills: set[str] = field(default_factory=set)


def _packages(text: str) -> set[str]:
    found = set()
    for token in re.findall(r"[\"']?([A-Za-z][\w.\-]+)\s*(?:[=<>~!]=|>|<|\"|'|$)", text, re.M):
        skill = PACKAGE_SKILLS.get(token.lower())
        if skill:
            found.add(skill)
    return found


def _notebook_text(path: Path) -> str:
    try:
        nb = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    cells = nb.get("cells", [])
    return "\n".join("".join(c.get("source", [])) for c in cells)[:20000]


def _read(path: Path) -> str:
    if path.suffix == ".ipynb":
        return _notebook_text(path)
    if path.suffix.lower() in (".pdf", ".docx"):
        return read_document(path)
    return path.read_text(encoding="utf-8", errors="ignore")[:30000]


_SKIP_DIRS = {".venv", "venv", "env", "node_modules", ".git", "site-packages", "dist", "build", ".cache",
              "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", "target", "data"}


def _project_files(folder: Path, limit: int = 60, max_depth: int = 4) -> list[Path]:
    """Walk the project, pruning dependency/cache folders *before* descending into them."""
    import fnmatch
    import os

    found: list[Path] = []
    base_depth = len(folder.parts)
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        if len(Path(dirpath).parts) - base_depth >= max_depth:
            dirnames[:] = []
        for name in sorted(filenames):
            if any(fnmatch.fnmatch(name, pat) for pat in _FILES):
                found.append(Path(dirpath) / name)
                if len(found) >= limit:
                    return found
    return found


def projects_from_folder(root: Path) -> list[Project]:
    """Each direct subfolder with recognisable files is a project; loose files form one more."""
    projects = []
    is_project = any((root / n).exists() for n in ("README.md", "README.rst", "README", "pyproject.toml",
                                                    "package.json", "requirements.txt"))
    candidates = [root] if is_project else \
        [p for p in sorted(root.iterdir()) if p.is_dir() and not p.name.startswith(".")] or [root]
    for folder in candidates:
        texts = [_read(f) for f in _project_files(folder)]
        if texts:
            projects.append(Project(folder.name, "\n".join(texts)))
    return projects


def projects_from_github(user: str, http: PoliteClient | None = None, max_repos: int = 30) -> list[Project]:
    http = http or PoliteClient(min_interval_s=1.0, max_requests=3 * max_repos + 5)
    headers = {"Accept": "application/vnd.github+json"}
    repos = http.get(f"https://api.github.com/users/{user}/repos", params={"per_page": 100, "sort": "pushed"},
                     headers=headers)
    repos.raise_for_status()
    projects = []
    for repo in [r for r in repos.json() if not r.get("fork")][:max_repos]:
        name = repo["name"]
        parts = [name, repo.get("description") or "", " ".join(repo.get("topics") or [])]
        langs = http.get(repo["languages_url"], headers=headers)
        skills = {GITHUB_LANG[l] for l in (langs.json() if langs.status_code == 200 else {}) if l in GITHUB_LANG}
        readme = http.get(f"https://api.github.com/repos/{user}/{name}/readme", headers=headers)
        if readme.status_code == 200 and readme.json().get("content"):
            parts.append(base64.b64decode(readme.json()["content"]).decode("utf-8", errors="ignore")[:20000])
        projects.append(Project(name, "\n".join(parts), skills))
    return projects


def analyse(projects: list[Project]) -> tuple[dict[str, int], Counter[str]]:
    evidence: Counter[str] = Counter()
    for p in projects:
        packages = _packages(p.text)
        if packages:            # a requirements file / pyproject means a Python project
            packages.add("python")
        p.skills |= set(find_in_text(p.text)) | packages
        evidence.update(p.skills)
    levels = {s: (3 if n >= 3 else 2) for s, n in evidence.items()}
    return levels, evidence


def profile_from_portfolio(inputs: list[str], llm: LLMClient | None = None,
                           http: PoliteClient | None = None) -> tuple[Profile, list[Project], Counter[str]]:
    projects: list[Project] = []
    for item in inputs:
        if item.startswith("github:"):
            projects += projects_from_github(item.split(":", 1)[1], http)
        else:
            path = Path(item).expanduser()
            if path.is_dir():
                projects += projects_from_folder(path)
            elif path.exists():
                projects.append(Project(path.stem, _read(path)))
            else:
                raise FileNotFoundError(item)
    levels, evidence = analyse(projects)
    combined = "\n\n".join(f"# {p.name}\n{p.text[:3000]}" for p in projects)
    # Language levels, years, summary: from the LLM if available, else rules.
    base = profile_from_text(combined, llm)
    skills = dict(base.skills)
    for s, lvl in levels.items():   # portfolio evidence wins over guesses from text
        skills[s] = max(lvl, skills.get(s, 0))
    skills = {s: v for s, v in skills.items() if canonical(s)}
    summary = base.summary if base.source == "upload" and llm and llm.available else \
        "Projects: " + "; ".join(f"{p.name} ({', '.join(sorted(p.skills)[:6])})" for p in projects[:12])
    profile = base.model_copy(update={"skills": skills, "summary": summary, "source": "portfolio"})
    return profile, projects, evidence
