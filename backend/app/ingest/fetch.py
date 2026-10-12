"""Download the transcript repository (public GitHub tarball) into a local
folder. Only `episodes/<slug>/transcript.md` entries are extracted and every
member path is validated, because the archive is untrusted input."""
import re
import tarfile
import tempfile
from pathlib import Path

import httpx

from app.core.logging import get_logger

log = get_logger("ingest.fetch")
MEMBER_RE = re.compile(r"^[^/]+/episodes/([A-Za-z0-9._-]+)/transcript\.md$")


def find_transcripts(root: Path) -> list[Path]:
    base = root / "episodes" if (root / "episodes").is_dir() else root
    return sorted(p for p in base.glob("*/transcript.md") if p.is_file())


def download_transcripts(url: str, dest: Path, timeout: float = 120.0) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "episodes").mkdir(exist_ok=True)
    log.info("download_start", url=url, dest=str(dest))
    with tempfile.TemporaryFile() as tmp:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 16):
                tmp.write(chunk)
        tmp.seek(0)
        count = 0
        with tarfile.open(fileobj=tmp, mode="r:gz") as tar:
            for member in tar:
                m = MEMBER_RE.match(member.name)
                if not (m and member.isfile()) or member.size > 50_000_000:
                    continue
                target = dest / "episodes" / m.group(1) / "transcript.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                f = tar.extractfile(member)
                if f is None:
                    continue
                target.write_bytes(f.read())
                count += 1
    log.info("download_done", episodes=count)
    return count
