"""Regenerate docker/requirements.lock.txt — the hash-pinned closure the image installs.

The judging image is built on a network that blocks `files.pythonhosted.org`,
where PyPI serves its wheels, so pip has to fetch from a proxy. That is safe
only if the bytes are checked, and this is what checks them:

1. **Resolve** the closure of `requirements.txt` for the image's own platform,
   by running `pip download` inside a container built from the SAME pinned base
   as the image. Resolving on the host would pick Windows wheels; resolving
   against a different index would pin versions the build's index may not have.
2. **Verify** every resolved file against `pypi.org/simple`, which publishes a
   sha256 per artifact and is reachable even where the file host is not. A
   proxy serving anything other than what PyPI published is caught here.
3. **Write** the lock, so the build can use `--require-hashes` and fail rather
   than install something else.

The hashes are properties of the artifacts, not of where they came from, so the
lock is equally valid on a network that can reach PyPI directly.

Usage:
    python src/experiments/lockgen.py
    python src/experiments/lockgen.py --index https://pypi.org/simple
"""
import argparse
import collections
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "docker" / "requirements.lock.txt"
DOCKERFILE = ROOT / "docker" / "Dockerfile"

# Whatever this machine's own pip resolves through. The image's default index.
DEFAULT_INDEX = "https://packagefeedproxy.microsoft.io/pypi/simple"
PYPI = "https://pypi.org/simple"


def base_image():
    """The `FROM` line of the real Dockerfile, so the resolver matches the image."""
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("FROM "):
            return line.split()[1]
    raise SystemExit("no FROM line in docker/Dockerfile")


def resolve(index):
    """{wheel filename: sha256}, resolved for the image's platform."""
    tag = "contract-risk-lockgen:tmp"
    dockerfile = f"""\
FROM {base_image()}
ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \\
 && apt-get install -y --no-install-recommends \\
        python3.12 python3.12-venv ca-certificates \\
 && rm -rf /var/lib/apt/lists/*
RUN python3.12 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt /tmp/requirements.txt
RUN pip download --no-cache-dir -r /tmp/requirements.txt -d /wheels \\
      --index-url {index}
"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "Dockerfile.lockgen"
        path.write_text(dockerfile, encoding="utf-8")
        print(f"resolving against {index} ...")
        run = subprocess.run(
            ["docker", "build", "-f", str(path), "-t", tag, str(ROOT)],
            capture_output=True, text=True)
        if run.returncode:
            sys.exit(run.stdout[-4000:] + run.stderr[-4000:])
        out = subprocess.run(
            ["docker", "run", "--rm", tag, "sh", "-c",
             "cd /wheels && sha256sum *"],
            capture_output=True, text=True, check=True)
        subprocess.run(["docker", "image", "rm", "-f", tag],
                       capture_output=True)

    found = {}
    for line in out.stdout.splitlines():
        if line.strip():
            h, name = line.split(None, 1)
            found[name.strip()] = h
    return found


def project_of(filename):
    return re.split(r"-\d", filename, 1)[0].replace("_", "-").lower()


def version_of(filename):
    m = re.match(r"^.+?-(\d[^-]*)-", filename) or \
        re.match(r"^.+?-(\d[^-]*)\.tar\.gz$", filename)
    if not m:
        raise SystemExit(f"cannot read a version out of {filename!r}")
    return m.group(1)


def verify(found):
    """Check every artifact against the sha256 PyPI itself publishes."""
    import httpx

    by_project = collections.defaultdict(list)
    for name, h in found.items():
        by_project[project_of(name)].append((name, h))

    client = httpx.Client(timeout=60, follow_redirects=True)
    bad = []
    for project in sorted(by_project):
        r = client.get(f"{PYPI}/{project}/",
                       headers={"Accept": "application/vnd.pypi.simple.v1+json"})
        r.raise_for_status()
        published = {f["filename"]: f["hashes"].get("sha256")
                     for f in r.json()["files"]}
        for name, h in by_project[project]:
            want = published.get(name)
            if want != h:
                bad.append((name, h, want))
    return bad


def write_lock(found, index):
    by_pin = collections.defaultdict(list)
    for name, h in sorted(found.items()):
        by_pin[(project_of(name), version_of(name))].append(h)

    out = [
        "# Generated. Do not edit by hand.",
        "#",
        "# The judging image is built where files.pythonhosted.org is blocked, so",
        "# pip fetches from a proxy. Every hash below was read from pypi.org/simple,",
        "# which is reachable, so an index serving anything other than what PyPI",
        "# published fails the build instead of shipping into a run.",
        "#",
        f"# Resolved against: {index}",
        "# Regenerate with:  python src/experiments/lockgen.py",
        "",
    ]
    for (project, version), hashes in sorted(by_pin.items()):
        out.append(f"{project}=={version} \\")
        out += [f"    --hash=sha256:{h} \\" for h in hashes[:-1]]
        out.append(f"    --hash=sha256:{hashes[-1]}")
    LOCK.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=DEFAULT_INDEX,
                    help=f"index to resolve against (default {DEFAULT_INDEX})")
    args = ap.parse_args()

    if not shutil.which("docker"):
        raise SystemExit("docker is needed: the closure is resolved inside the "
                         "image's own base, not on this machine")

    found = resolve(args.index)
    print(f"resolved {len(found)} artifact(s)")

    bad = verify(found)
    for name, got, want in bad:
        print(f"  MISMATCH {name}\n    index {got}\n    pypi  {want or '(not published)'}")
    if bad:
        raise SystemExit(f"{len(bad)} artifact(s) do not match PyPI — lock not written")
    print(f"verified {len(found)} artifact(s) against pypi.org")

    write_lock(found, args.index)
    pins = sum(1 for line in LOCK.read_text(encoding="utf-8").splitlines()
               if "==" in line)
    print(f"wrote {LOCK.relative_to(ROOT)} ({pins} pins)")


if __name__ == "__main__":
    main()
