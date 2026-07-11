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
  --dependency-graph ../odm-asr-map-backed-example/out/redux-odm-cli-validation/dependency-graph/rule-dependency-graph.json \
  --site-name "ODM Application Documentation"
```

The `--ruleflow-index` option is optional, but recommended for customer delivery. When supplied, the generated site starts from a Ruleflows section and creates one page per ruleflow showing operations, RuleTasks, rules, direct subflows, and recursively nested subflows. Ruleflow pages link forward into matching knowledge-base rule pages, and copied rule pages are enriched with a Ruleflow Usage section that links back to the ruleflows and tasks that reference the rule.

The `--dependency-graph` option is also recommended. It enriches each ruleflow rule with deterministic evidence from `rule-dependency-graph.json`, including reads, writes, and created/inserted/required objects. This keeps ruleflow pages useful even when a full Markdown rule document has not been generated yet.

Outputs:

- `build/mkdocs/` - temporary MkDocs project
- `build/site/` - generated HTML site
- `dist/odm-application-documentation.zip` - distributable ZIP

Generated customer navigation includes:

- `ruleflows/index.html` - all ruleflows with task/subflow/rule counts
- `ruleflows/*.html` - one page per ruleflow with nested task/subflow tree, previous/next ruleflow navigation, doc availability, reads, writes, and object evidence
- copied rule pages include `Ruleflow Usage` backlinks when the ruleflow index source file matches a knowledge-base Markdown page
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
