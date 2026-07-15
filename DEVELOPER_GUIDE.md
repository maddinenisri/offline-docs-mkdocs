# Developer Guide: Static Customer Documentation With Explicit Navigation

This guide explains how to turn a customer `docs/` folder into an offline static HTML site using `scripts/build_static_docs.py`.

Use this mode when the customer already has Markdown documentation and you need a self-contained HTML site with:

- `START_HERE.html`
- preserved folder structure
- explicit left navigation
- breadcrumbs
- previous and next links
- ruleflow context pages
- no requirement for customers to run Python, MkDocs, or a web server

## Recommended Source Layout

Keep all source Markdown under one `docs/` folder.

```text
docs/
├── index.md
├── README.md
├── nav.json
├── ruleflows/
│   ├── processClaim.md
│   ├── fraudReview.md
│   └── verification.md
└── ruleproject/
    └── ClaimProcessing/
        └── rules/
            ├── fraudReview/
            │   └── scoringEngine/
            │       ├── index.md
            │       ├── README.md
            │       └── fraud_score.md
            └── verification/
                └── coverage/
                    ├── index.md
                    └── README.md
```

Use these conventions:

- `index.md` is the folder landing page.
- `README.md` is supporting detail for the folder.
- Other Markdown files are detail pages, rule pages, summaries, or additional notes.
- `nav.json` controls only the left navigation.
- Paths in Markdown should be relative where possible.

## `index.md` And `README.md`

When a folder has both `index.md` and `README.md`, do not list both in the left navigation.

Recommended pattern:

```text
ruleproject/ClaimProcessing/rules/fraudReview/scoringEngine/
├── index.md
├── README.md
└── fraud_score.md
```

In `nav.json`, point the task/package entry to `index.md`:

```json
{
  "type": "task",
  "label": "scoringEngine",
  "path": "ruleproject/ClaimProcessing/rules/fraudReview/scoringEngine/index.md"
}
```

The builder still converts both files:

```text
index.md  -> index.html
README.md -> README.html
```

It also adds generated cross-links:

```text
index.html  -> Open README details
README.html -> Back to folder index
```

This prevents duplicate sidebar entries while keeping README content reachable.

## Left Navigation Model

The left navigation is intentionally explicit. The builder does not scan every Markdown file and guess the sidebar for production builds.

Only entries listed in `nav.json` appear in the sidebar.

Other Markdown files are still converted to HTML and can be reached through:

- links inside Markdown content
- generated `README` and `index` cross-links
- ruleflow usage backlinks
- context pages under `_contexts/`

## Sample `nav.json`

Create `docs/nav.json`.

This example has one root ruleflow with two sibling subflows:

```json
{
  "title": "ODM Ruleflow Documentation",
  "roots": [
    {
      "type": "ruleflow",
      "label": "processClaim",
      "path": "ruleflows/processClaim.md",
      "children": [
        {
          "type": "subflow",
          "label": "fraudReview",
          "path": "ruleflows/fraudReview.md",
          "children": [
            {
              "type": "task",
              "label": "scoringEngine",
              "path": "ruleproject/ClaimProcessing/rules/fraudReview/scoringEngine/index.md",
              "children": [
                {
                  "type": "rule",
                  "label": "fraud_score",
                  "path": "ruleproject/ClaimProcessing/rules/fraudReview/scoringEngine/fraud_score.md"
                }
              ]
            }
          ]
        },
        {
          "type": "subflow",
          "label": "verification",
          "path": "ruleflows/verification.md",
          "children": [
            {
              "type": "task",
              "label": "coverage",
              "path": "ruleproject/ClaimProcessing/rules/verification/coverage/index.md"
            }
          ]
        }
      ]
    }
  ]
}
```

The generated sidebar shape is:

```text
processClaim
├── fraudReview
│   └── scoringEngine
│       └── fraud_score
└── verification
    └── coverage
```

## Multiple Root Ruleflows

If the customer has five main ruleflows, create five entries under `roots`.

```json
{
  "title": "ODM Ruleflow Documentation",
  "roots": [
    {
      "type": "ruleflow",
      "label": "processClaim",
      "path": "ruleflows/processClaim.md",
      "children": []
    },
    {
      "type": "ruleflow",
      "label": "renewPolicy",
      "path": "ruleflows/renewPolicy.md",
      "children": []
    },
    {
      "type": "ruleflow",
      "label": "cancelPolicy",
      "path": "ruleflows/cancelPolicy.md",
      "children": []
    },
    {
      "type": "ruleflow",
      "label": "endorsePolicy",
      "path": "ruleflows/endorsePolicy.md",
      "children": []
    },
    {
      "type": "ruleflow",
      "label": "reinstatePolicy",
      "path": "ruleflows/reinstatePolicy.md",
      "children": []
    }
  ]
}
```

Each root can have its own nested subflows, tasks, and rules.

## Shared Tasks And Shared Subflows

If the same task or rule is used by more than one ruleflow, list it under each ruleflow path in `nav.json`.

Example:

```json
{
  "type": "task",
  "label": "eligibilityCheck",
  "path": "ruleproject/Common/rules/eligibility/index.md"
}
```

The canonical page is generated once at its original path. The builder also creates context pages so users can distinguish where the same page is used.

Example paths:

```text
processClaim -> fraudReview -> scoringEngine -> fraud_score
renewPolicy -> fraudReview -> scoringEngine -> fraud_score
```

This is important when the same rule behaves the same technically but has different business meaning depending on the ruleflow.

## Navigation Path Rules

Each `path` in `nav.json` must resolve under the `--source` folder.

Valid:

```json
{
  "path": "ruleflows/processClaim.md"
}
```

Also valid when the directory contains `index.md` or `README.md`:

```json
{
  "path": "ruleproject/ClaimProcessing/rules/fraudReview/scoringEngine"
}
```

Directory paths resolve in this order:

```text
index.md
README.md
```

Prefer explicit file paths in customer projects because they are easier to review.

## Build A Customer ZIP

From this repository:

```bash
cd /path/to/offline-docs-mkdocs
. .venv/bin/activate

python scripts/build_static_docs.py \
  --source /path/to/customer/docs \
  --nav /path/to/customer/docs/nav.json \
  --site-name "Customer ODM Documentation" \
  --zip-name customer-odm-documentation.zip
```

The ZIP is written to:

```text
dist/customer-odm-documentation.zip
```

Deliver the ZIP to the customer. After extracting it, they open:

```text
START_HERE.html
```

## Build Without A ZIP

Use this for local review or when another process will package the site:

```bash
python scripts/build_static_docs.py \
  --source /path/to/customer/docs \
  --nav /path/to/customer/docs/nav.json \
  --site-name "Customer ODM Documentation" \
  --build-dir build/customer-static-site \
  --no-zip
```

Open:

```text
build/customer-static-site/START_HERE.html
```

## Validate The Site

The builder validates offline links automatically. You can also run validation manually:

```bash
python scripts/validate_offline_site.py \
  --site-root build/customer-static-site
```

Expected output:

```text
Offline documentation links are valid.
```

For a ZIP delivery, extract the ZIP and validate the extracted folder:

```bash
mkdir -p dist/extracted/customer-odm-documentation
unzip -q dist/customer-odm-documentation.zip \
  -d dist/extracted/customer-odm-documentation

python scripts/validate_offline_site.py \
  --site-root dist/extracted/customer-odm-documentation
```

## Delivery Rule

Deliver only generated HTML output.

Do deliver:

```text
customer-odm-documentation.zip
```

Do not deliver:

```text
docs/
Markdown source
nav.json
build metadata
```

When `nav.json` is inside the source folder, the builder uses it during generation but excludes it from the generated site and ZIP.

## Developer Checklist

Before delivery:

- Confirm the source folder has a valid `nav.json`.
- Confirm each root ruleflow appears under `roots`.
- Confirm package/task nav entries point to `index.md`, not `README.md`.
- Confirm folders with both `index.md` and `README.md` generate cross-links.
- Confirm shared tasks are intentionally listed under each consuming ruleflow.
- Build with ZIP mode.
- Extract the ZIP.
- Validate the extracted site.
- Open `START_HERE.html` and inspect the left navigation.
- Confirm no `nav.json` is present in the generated site or ZIP.
