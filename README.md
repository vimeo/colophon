# Colophon

**A toolkit for auditing third-party open source application dependencies for license compliance.**

## Legal Disclaimer

**NO LEGAL ADVICE.** Nothing in this repository, documentation, or output constitutes legal advice. No guarantees are provided regarding the accuracy, thoroughness, or completeness of the data produced by these scripts.

This toolkit is intended to assist Security, Engineering, GRC, Legal, and Compliance teams in gathering data. All decisions regarding license compliance, risk tolerance, and remediation must be made in consultation with your organization's legal counsel.

---

## Overview

Colophon (`Compliance Of Licenses Of Packages & HONoring Notices`) is a set of utilities designed to help organizations audit the "standing water" of their existing open source dependencies. It focuses on **application-level dependencies** (libraries used in your code), not OS-level or container-level packages.

**Scope & Strategy**
Effective license compliance requires two distinct solutions:
1.  **Stop the Leak:** Automated tooling in your SDLC (CI/CD gates) to block *new* Pull Requests containing unapproved licenses.
2.  **Bail the Water:** Auditing the *existing* backlog of repositories and dependencies. **Colophon is designed for this specific purpose.**

Ideally, teams should perform this audit on at least a yearly basis.

**Note:** This is not a "one-click" solution. It requires a working Python environment, access to GitHub APIs, and—most importantly—collaboration between Engineering, Security, and Legal stakeholders to define risk and classify licenses.

---

## Workflow Overview
```text
[ GitHub API ] 
      |
      v
(fetch_repos.py) --> [ all_repos.csv ] --+                                  +- (quer_packages.sql)
                                         |                                  |
(fetch_deps.py) --> [ all_deps.csv ] ----+--> (schema.sql) --> [ oss.db ] <-+- (query_repos.sql)
                                         |
(Manual Entry) --> [ licenses.csv ] -----+
```

---

## Prerequisites

### 1. Environment Setup
Ensure you have a working Python 3 environment. We recommend using a virtual environment:

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`

# Install required packages
pip install -r requirements.txt
```

### 2. GitHub Access

For each GitHub organization you audit, you need a **Fine-grained Personal Access Token**.

1. Go to **Settings > Developer Settings > Personal access tokens > Fine-grained tokens**.
2. Click **Generate new token**.
3. **Resource Owner:** Select the Organization you are auditing (NOT your personal user). This is `CRITICAL`.
4. **Repository Access:** Select "All repositories".
5. **Permissions:** Grant **Read-only** access for:
    * `Administration`
    * `Contents`
    * `Custom properties`
    * `Metadata`

### 3. Enable Dependency Graph
For these scripts to function correctly, GitHub's Dependency Graph must be enabled for all repos within each org.

* Navigate to your Organization Settings > Code Security and Analysis.

* Enable Dependency Graph as a required default setting for all exisitng and new repos in the org.

* *Note:* This does not break anything and incurs no additional cost.

## The Audit Process
### Step 1: Fetch Repositories
Collect metadata on all repositories within your target organization(s). This script generates a CSV containing useful metadata for prioritization (archived status, visibility, languages, etc.).

```bash
python fetch_repos.py --org <YOUR_ORG_NAME> --token <YOUR_GITHUB_TOKEN>
```
* Resume Support: If the script fails (e.g., network issues), use --start-index <N> to restart from a specific point.

* Multiple Orgs: Run this command separately for every organization you control.

### Step 2: Concatenate Repository Data
If you fetched data for multiple organizations, combine them into a single master list.

```bash
source helper.sh
concat_csvs repos_org1.csv repos_org2.csv repos_org3.csv > all_repos.csv
```

### Step 3: Fetch Dependencies
This step downloads the license data for every repository.

**Note:** This process combines data from the GitHub SBOM API (high confidence) and the GraphQL API (higher visibility but noisier). It is designed to be thorough.

```bash
python fetch_deps.py --org <YOUR_ORG_NAME> --token <YOUR_GITHUB_TOKEN>
```
* *Warning:* This script can take a significant amount of time for large organizations.

* Use --start-index <N> to resume at a specific index if interrupted.

* Concatenate these outputs similarly to Step 2 if you have multiple orgs.
```bash
source helper.sh
concat_csvs dependencies_org1.csv dependencies_org2.csv dependencies_org3.csv > all_deps.csv
```

### Step 4: Classify Licenses
You must define which licenses are acceptable for your organization. Create a licenses.csv file mapping license identifiers to a status: `GO`, `STOP`, `CAUTION`, or `UNCLASSIFIED`. Make sure that the CSV file includes the specific line `N/A,UNDETECTED` (since the Github API used "N/A" to indicate that it could not detect or recongize the license for a particular package). Also make sure to include the header line as the first row (i.e., `License,Status`).

**Format Example (licenses.csv):**

```csv
License,Status
MIT,GO
MIT-Modern-Variant,GO
MIT-0,GO
MPL-2.0,CAUTION
mpich2,STOP
mzzz,UNCLASSIFIED
N/A,UNDETECTED
```

**Helpers:** To generate a list of all unique licenses found in your scan so you can classify them (where `all_deps.csv` is the output of Step 3).

```bash
source helper.sh
unique_licenses all_deps.csv
```

To find licenses in your scan that are missing from your existing classification file (where `all_deps.csv` is the output of Step 3 and `licenses.csv` is the output of Step 4).
```bash
source helper.sh
unrecognized_licenses all_deps.csv licenses.csv
```

### Step 5: Database Analysis
Import your data into a SQLite database. 

**Important:** The `schema.sql` script assumes your CSV files are named exactly `all_repos.csv`, `all_deps.csv`, and `licenses.csv`. Rename your files if necessary before running this command.

```bash
# Initialize DB and import data
sqlite3 oss.db < schema.sql
```

**Run Analysis Queries: Run the provided SQL queries to generate actionable reports.**

1. Identify Problematic Repositories: Finds repos containing at least one violation.

```bash
sqlite3 -csv oss.db < query_repos.sql > result_repos.csv
```

2. Identify Problematic Packages: Lists specific package violations with context.

```bash
sqlite3 -csv oss.db < query_packages.sql > result_packages.csv
```

### Step 6: Remediation & Annotation
Export `result_repos.csv` and `result_packages.csv` to a spreadsheet. Share this with key stakeholders (engineering leaders, legal, compliance, security, etc.). You may need to add a few columns to the spreadsheet to aid with annotations. 

**Potential Heuristics for Risk Assessment:**

* Distribution: Is this code distributed to clients (mobile apps, browser JS, desktop applications, etc.) or strictly backend?

* Production: Is this a production repo or an internal playground/tool?

* Dev vs. Prod Dependency: Check the manifest_url provided in the output. Is the package a devDependency (often lower risk) or a production dependency? Note that this is only available for some packages (based on how their package manager / ecosystem work).

* Repository Status: Is the repo archived? Should it be archived? When was the last push? Is anyone actually using the repo?

**Definitions: Before starting, align with Legal on:**

* "Production": Does this include helper scripts? Internal bookkeeping tools? Internal hackathon projects?

* "Distributed": Note that some modern licenses restrict usage even if the code is not distributed (SaaS loopholes), such as AGPLv3.

## Pitfalls & Considerations
1. **Rate Limiting:** GitHub API rate limits can block execution. The scripts include retry logic with backoff, but large scans may still require manual resumption using the --start-index flag.

2. **"NOASSERTION (Other)":** If you see NOASSERTION (Other) for the license name, a license was found but could not be identified against the SPDX database. These often require manual review (custom licenses or modified headers).

3. **SBOM vs. GraphQL:** GitHub's SBOM export has high confidence but misses many licenses. We deliberately query the GraphQL endpoint to fill gaps. This results in more data, but potentially more noise. As a future improvement to this project, we should provide a command line argument in the `fetch_deps.py` to make fetching the from the GraphQL endpoint optional. Currently the script always fetches from bother hte SBOM and GraphQL endpoints.

4. **Platform Support:** Currently supports GitHub only (not Bitbucket, Gitlab, etc).

## Credits

Built by Ed Sullivan ([@ed-sulli](https://github.com/ed-sulli)).

The engineering and security teams at [Vimeo](https://github.com/vimeo) have provided a lot of encouragement.
