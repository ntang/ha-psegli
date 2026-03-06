# Release Process

This repository uses a manual release flow. Follow this checklist for every release.

## 1) Prepare Changelog First

File: `addons/psegli-automation/CHANGELOG.md`

1. Keep `## Unreleased` at the top for future work only.
2. Move completed release notes under a new heading:
   - `## <MAJOR.MINOR.PATCH[.HOTFIX]>` (examples: `## 2.5.1`, `## 2.5.1.1`)
3. Ensure the section includes everything shipping in that release (integration + add-on user-visible changes).

## 2) Bump Version Everywhere

From repo root:

```bash
python3 scripts/sync_version.py --set <MAJOR.MINOR.PATCH[.HOTFIX]>
```

`VERSION` is the single source of truth; this command syncs all versioned files.

## 3) Verify Before Publish

From repo root:

```bash
.venv/bin/python -m pytest -q
```

If you use a different local venv name, run the same command from that environment.

## 4) Commit + Push Version/Docs Changes

```bash
git add VERSION repository.yaml custom_components/psegli/manifest.json \
  addons/psegli-automation/config.yaml addons/psegli-automation/build.yaml \
  addons/psegli-automation/run.py addons/psegli-automation/README.md \
  addons/psegli-automation/CHANGELOG.md
git commit -m "chore(release): bump version to <MAJOR.MINOR.PATCH[.HOTFIX]>"
git push
```

## 5) Create and Push Tag

```bash
git tag -a v<MAJOR.MINOR.PATCH[.HOTFIX]> -m "Release <MAJOR.MINOR.PATCH[.HOTFIX]>"
git push origin v<MAJOR.MINOR.PATCH[.HOTFIX]>
```

## 6) Publish GitHub Release With Changelog Body

Create or update release notes from the exact changelog section for that version.

Example:

```bash
gh release create v<MAJOR.MINOR.PATCH[.HOTFIX]> --title "v<MAJOR.MINOR.PATCH[.HOTFIX]>" --generate-notes
gh release edit v<MAJOR.MINOR.PATCH[.HOTFIX]> --notes "<paste changelog section text here>"
```

Required: release body should contain the actual `## <MAJOR.MINOR.PATCH[.HOTFIX]>` notes (not only an auto-generated compare link).

## 7) Verify Published Release

```bash
gh release view v<MAJOR.MINOR.PATCH[.HOTFIX]> --json tagName,name,publishedAt,url,body
gh run list --limit 10
```

Note: this repository currently has a `Tests` workflow only; there is no separate in-repo publish/build workflow file under `.github/workflows/`.
