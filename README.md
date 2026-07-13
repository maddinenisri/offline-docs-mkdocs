# Offline ODM Documentation Builder

Builds a MkDocs HTML portal from generated ODM Markdown and packages only the generated site as a ZIP.

The ZIP is meant for business users. It includes `START_HERE.html`, `index.html`, local assets, and explicit `.html` links. Users do not need Python, MkDocs, a web server, or the Markdown source.

For the current ruleflow documentation product, this repository is the publishing step only. Generate the customer-facing Markdown first with `redux-odm-cli generate-ruleflow-docs`, then point this builder at that final `ruleflow-documentation` folder.

## Setup

```bash
cd /path/to/offline-docs-mkdocs
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Two-Repo Workflow

First run the CLI repository to generate ruleflow documentation:

```bash
cd /path/to/redux-odm-cli
npm install
npm run build

node dist/cli.js build-knowledge-base \
  --workspace /path/to/client-odm-repo \
  --rule-project path/to/ruleproject \
  --out out/manual-llm-rule-requirements/knowledge-base

node dist/cli.js generate-ruleflows \
  --workspace /path/to/client-odm-repo \
  --rule-project path/to/ruleproject \
  --rule-docs out/manual-llm-rule-requirements/knowledge-base \
  --out out/manual-llm-rule-requirements/ruleflow-index \
  --index-only

node dist/cli.js generate-ruleflow-docs \
  --workspace /path/to/client-odm-repo \
  --rule-project path/to/ruleproject \
  --ruleflow-index out/manual-llm-rule-requirements/ruleflow-index/indexes/ruleflow-task-index.json \
  --knowledge-base out/manual-llm-rule-requirements/knowledge-base \
  --dependency-graph out/manual-llm-rule-requirements/knowledge-base/rule-dependency-graph.json \
  --out out/manual-llm-rule-requirements/ruleflow-documentation
```

Then run this offline-docs repository to package the final Markdown folder:

```bash
cd /path/to/offline-docs-mkdocs
. .venv/bin/activate

python scripts/build_offline_docs.py \
  --source /path/to/client-odm-repo/out/manual-llm-rule-requirements/ruleflow-documentation \
  --site-name "ODM Application Ruleflow Documentation" \
  --zip-name odm-application-ruleflow-documentation.zip
```

Open or distribute:

```text
dist/odm-application-ruleflow-documentation.zip
```

After extracting the ZIP, open:

```text
START_HERE.html
```

## Source Folder Rule

Use `--source` for the final generated documentation folder that should become the website. For customer ruleflow docs, use:

```text
/path/to/client-odm-repo/out/manual-llm-rule-requirements/ruleflow-documentation
```

Do not use the raw knowledge-base folder as `--source` for customer ruleflow delivery. The raw knowledge base can contain folder summaries, prompt/debug artifacts, generation reports, and intermediate pages. `generate-ruleflow-docs` performs the clean join of:

```text
ruleflow index + ODM source rules + knowledge-base notes + dependency graph
```

and writes the customer-facing ruleflow-first documentation.

## Outputs

- `build/mkdocs/` - temporary MkDocs project
- `build/site/` - generated HTML site
- `dist/<zip-name>` - distributable ZIP

Generated customer navigation includes:

- `ruleflows/index.html` - all ruleflows with task/subflow counts
- `ruleflows/<ruleflow>/index.html` - one page per ruleflow with tasks, subflows, and rule summary
- `ruleflows/<ruleflow>/tasks/<task>.html` - task-level drilldown
- `ruleflows/<ruleflow>/rules/<rule>.html` - source-backed rule logic, data used/updated, and optional knowledge-base notes
- `catalogs/index.html` - secondary navigation/index page

## Distribution Rule

Distribute only the ZIP under `dist/`. Do not distribute `build/mkdocs/docs/` or any Markdown source.

## Offline Behavior

The generated `mkdocs.yml` sets:

```yaml
use_directory_urls: false
```

This makes MkDocs emit links like `ruleproject/example.html` instead of `ruleproject/example/`, which is required when users open the site through `file://`.
