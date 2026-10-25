# Releasing

## One-time setup

1. Create the GitHub repository and push `main`.
2. On https://pypi.org, add a *pending trusted publisher* for the project `cldf-mcp`:
   owner/repo of this repository, workflow `publish.yml`, environment `pypi`.
3. In the GitHub repository settings, create an environment named `pypi`.
4. If your GitHub username or repository name differs from the ones in `pyproject.toml`,
   `server.json` and the `mcp-name` comment at the top of `README.md`, update all three. The
   MCP Registry checks that the `mcp-name` in the README published on PyPI matches `server.json`.

## Each release

1. Update `__version__` in `src/cldf_mcp/__init__.py`, `version` in `pyproject.toml`, both
   `version` fields in `server.json`, and `CHANGELOG.md`.
2. Run `pytest` and `pytest -m network`.
3. Tag and publish a GitHub release named `vX.Y.Z`. The `publish.yml` workflow checks the tag
   against the package version, runs the tests, builds and uploads to PyPI.
4. Publish the metadata to the MCP Registry (after the PyPI upload is live):

   ```bash
   brew install mcp-publisher   # or download from github.com/modelcontextprotocol/registry/releases
   mcp-publisher login github
   mcp-publisher publish
   ```

## Updating a dataset release

1. Change `version` and `base_url` for the dataset in `src/cldf_mcp/datasets.toml`.
2. Recompute checksums from the files served at the new tag (not from a git checkout: the WALS
   repository converts line endings on checkout, which changes the hashes):

   ```bash
   for f in languages.csv parameters.csv codes.csv values.csv; do
     curl -sSfL "https://raw.githubusercontent.com/cldf-datasets/wals/NEW_TAG/cldf/$f" | sha256sum
   done
   ```

3. Update expected counts in `tests/test_network.py` if the data changed, and note it in the
   changelog. Existing installs rebuild automatically because the registry fingerprint changes.
