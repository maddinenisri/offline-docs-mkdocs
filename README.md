# Offline ODM Documentation Builder

Builds a MkDocs HTML portal from generated ODM knowledge-base Markdown and packages only the generated site as a ZIP.

The ZIP is meant for business users. It includes `START_HERE.html`, `index.html`, local assets, and explicit `.html` links. Users do not need Python, MkDocs, a web server, or the Markdown source.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Build

```bash
python scripts/build_offline_docs.py \
  --source ../odm-asr-map-backed-example/out/manual-llm-rule-requirements/knowledge-base \
  --ruleflow-index ../odm-asr-map-backed-example/out/redux-odm-cli-validation/ruleflow-index/indexes/ruleflow-task-index.json \
  --site-name "ODM Application Documentation"
```

The `--ruleflow-index` option is optional, but recommended for customer delivery. When supplied, the generated site starts from a Ruleflows section and creates one page per ruleflow showing operations, RuleTasks, rules, direct subflows, and recursively nested subflows. It also creates reverse catalogs from rule to ruleflow.

Outputs:

- `build/mkdocs/` - temporary MkDocs project
- `build/site/` - generated HTML site
- `dist/odm-application-documentation.zip` - distributable ZIP

Generated customer navigation includes:

- `ruleflows/index.html` - all ruleflows with task/subflow/rule counts
- `ruleflows/*.html` - one page per ruleflow with nested task/subflow tree
- `catalogs/ruleflow-task-catalog.html` - root ruleflow, owning ruleflow, task, and rule mapping
- `catalogs/rule-to-ruleflow-catalog.html` - reverse lookup from rule to ruleflows

## Distribution Rule

Distribute only the ZIP under `dist/`. Do not distribute `build/mkdocs/docs/` or any Markdown source.

## Offline Behavior

The generated `mkdocs.yml` sets:

```yaml
use_directory_urls: false
```

This makes MkDocs emit links like `ruleproject/example.html` instead of `ruleproject/example/`, which is required when users open the site through `file://`.
