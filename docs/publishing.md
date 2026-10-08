# Publishing a release

Publishing uses the existing GitHub repository secret `PYPL`. Its value is a
PyPI API token. It is passed directly to the official PyPA publishing action;
tests and package-build jobs do not receive it.

To publish a new version:

1. Update the matching versions in `pyproject.toml` and
   `pageharvest/__init__.py`, then merge the reviewed changes into `main`.
2. Open GitHub **Actions → Publish to PyPI → Run workflow**.
3. Select `main`, enter the exact version, and run the workflow.

The workflow reruns Windows/Linux tests and browser integration, builds the
source archive and wheel, validates metadata, checks installation outside the
checkout, then uploads those artifacts to PyPI. A requested version mismatch
fails before upload. Only manual runs on `main` can publish.

Existing versions are not overwritten or silently skipped. If an upload fails,
inspect the publishing step before retrying; do not reuse a version for changed
package contents. For a normal update, increment the version first.

After a successful upload, verify in a new virtual environment:

```sh
python -m venv .pypi-check
```

Windows:

```powershell
.pypi-check\Scripts\python -m pip install --index-url https://pypi.org/simple pageharvest
.pypi-check\Scripts\python -m pageharvest --help
```

Linux/macOS:

```sh
.pypi-check/bin/python -m pip install --index-url https://pypi.org/simple pageharvest
.pypi-check/bin/python -m pageharvest --help
```

Inspect package imports from outside the source checkout when verifying the
published artifact. PyPI's project page is https://pypi.org/project/pageharvest/.
