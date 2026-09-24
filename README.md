# NJ Young People AA

A public, mobile-friendly meeting map with search, day filter, and Apple Maps directions. It reads a static `meetings.json` snapshot refreshed daily by GitHub Actions. **The repository starts with an empty snapshot, not invented meeting data.**

## Publish

1. Create a **public** GitHub repository (for example `nj-young-people-aa-map`). Upload **all files and folders**, including `.github/workflows/update.yml`, preserving their paths. The easiest route on a PC is to unzip this package and upload its contents with Git or GitHub Desktop; the `.github` directory is hidden on some file browsers.
2. Open the repository's **Actions** tab, enable workflows if prompted, choose **Refresh AA meetings**, and select **Run workflow**. Check the run log. If the updater fails, the empty snapshot is intentionally preserved; consult the log and fix the parser or source access before publishing.
3. Once `meetings.json` contains actual meetings, open **Settings → Pages → Build and deployment**, choose **Deploy from a branch**, select `main` and `/ (root)`, then Save. The site will be at `https://YOUR-USERNAME.github.io/REPOSITORY-NAME/` after Pages finishes deploying.
4. Open the URL in Safari on an iPhone. Share → Add to Home Screen.

## Updating and reliability

The workflow runs daily at **10:17 UTC** (roughly 6:17 a.m. EDT / 5:17 a.m. EST), subject to GitHub Actions scheduling delays. You can also run it manually from Actions. Scheduled workflows in public repositories can be disabled after prolonged inactivity; check Actions periodically.

The updater preserves the last published snapshot if a source fails, parses zero matching meetings, or the total falls by more than 35%. That guard may require manual review when genuine meeting counts decline. Unmapped meetings remain visible under the map's disclosure panel. `geocodes.json` caches accepted coordinates; do not hand-edit it unless correcting a verified address. The V10 South/Cape 10-column parser is retained, but **those sites' live HTML has not been independently verified**. If it parses zero meetings, review their current markup rather than publishing a partial map. Identical coordinates may indicate inaccurate source data; the map offsets overlapping pins for visibility, not accuracy.

This is an independent directory, not an official AA or intergroup publication. Verify meeting times and locations with the linked intergroups. Check each site's terms and request rates before broad distribution.

## Local test

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python update_meetings.py
python -m http.server 8000
```

Open `http://localhost:8000/`. Do not open `index.html` directly as a `file://` URL: the browser may block fetching the JSON file.
