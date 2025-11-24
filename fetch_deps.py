import os
import sys
import time
import csv
import logging
import argparse
import requests
import random
import re
from typing import List, Dict, Any, Optional, Tuple

# --- Configuration ---
GITHUB_API_URL: str = "https://api.github.com"
GITHUB_GQL_URL: str = "https://api.github.com/graphql"
PROACTIVE_DELAY_SECONDS: int = 2
GQL_PAGE_SIZE: int = 20 # Page size to avoid timeouts
# ---------------------

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] - %(message)s",
    stream=sys.stdout,
)

# --- GraphQL Query ---
GQL_DEPENDENCY_QUERY = """
query($owner:String!, $name:String!, $manifestsAfter: String, $pageSize: Int!) {
  repository(owner:$owner, name:$name) {
    dependencyGraphManifests(first: $pageSize, after: $manifestsAfter) {
      pageInfo {
        endCursor
        hasNextPage
      }
      nodes {
        filename
        dependencies(first: $pageSize) {
          pageInfo {
            endCursor
            hasNextPage
          }
          nodes {
            packageManager
            packageName
            requirements
            packageUrl
            repository {
              nameWithOwner
              licenseInfo {
                spdxId
                name
                url
              }
            }
          }
        }
      }
    }
  }
}
"""

def get_github_session(token: str) -> requests.Session:
    """
    Initializes and returns a requests.Session with necessary GitHub API headers.
    """
    session = requests.Session()
    session.headers.update({
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return session


def handle_api_retry(
    response: requests.Response,
    context_msg: str
) -> Tuple[bool, int]:
    """
    Handles API rate limiting and retries for both REST and GraphQL.
    
    Returns:
        A tuple of (should_retry: bool, sleep_duration: int)
    """
    sleep_duration = 0
    should_retry = False

    # Primary rate limit
    if response.status_code == 403 and 'X-RateLimit-Remaining' in response.headers and int(response.headers['X-RateLimit-Remaining']) == 0:
        reset_timestamp = int(response.headers['X-RateLimit-Reset'])
        sleep_duration = max(0, reset_timestamp - time.time()) + 5
        logging.warning(f"Primary rate limit hit for {context_msg}. Waiting {sleep_duration:.2f}s for reset.")
        should_retry = True

    # Secondary rate limit
    elif response.status_code == 429 or (response.status_code == 403 and "Retry-After" in response.headers):
        if "Retry-After" in response.headers:
            sleep_duration = int(response.headers["Retry-After"]) + 1
        else:
            sleep_duration = -1 # Signal for exponential backoff
        
        logging.warning(f"Secondary rate limit hit for {context_msg}. Retrying.")
        should_retry = True
    
    # GQL rate limit in body
    elif response.status_code == 200 and 'errors' in response.json():
        try:
            errors = response.json()['errors']
            if any(err.get('type') == 'RATE_LIMITED' for err in errors):
                logging.warning(f"GraphQL rate limit reported in body for {context_msg}. Retrying.")
                sleep_duration = -1 # Signal for exponential backoff
                should_retry = True
        except requests.exceptions.JSONDecodeError:
            pass 

    return should_retry, sleep_duration


def get_all_organization_repos(session: requests.Session, org: str) -> List[Dict[str, Any]]:
    """
    Fetches all repositories for the given organization, handling pagination and primary rate limits.
    """
    all_repos = []
    logging.info(f"Fetching repositories for organization: {org}")
    
    url = f"{GITHUB_API_URL}/orgs/{org}/repos"
    page = 1
    while url:
        try:
            logging.info(f"Fetching repos for '{org}', page {page}...")
            response = session.get(url, params={"per_page": 100, "type": "all"})
            
            should_retry, sleep_duration = handle_api_retry(response, f"org repo list page {page}")
            if should_retry:
                time.sleep(sleep_duration if sleep_duration > 0 else 5)
                continue 

            response.raise_for_status()
            data = response.json()
            all_repos.extend(data)
            
            url = response.links.get("next", {}).get("url")
            page += 1

        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to fetch repositories for {org}: {e}")
            break
    return all_repos


def get_repo_sbom(session: requests.Session, full_repo_name: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves the SBOM for a single repo with robust retry logic for rate limiting.
    """
    url = f"{GITHUB_API_URL}/repos/{full_repo_name}/dependency-graph/sbom"
    base_delay = 5
    max_delay = 3600  # 1 hour
    retries = 0

    while True:
        try:
            response = session.get(url)

            should_retry, sleep_duration = handle_api_retry(response, f"SBOM for {full_repo_name}")
            if should_retry:
                if sleep_duration == -1: # Exponential backoff signal
                    sleep_duration = (base_delay * (2 ** retries)) + random.uniform(0, 1)
                    sleep_duration = min(sleep_duration, max_delay)
                    retries += 1
                else:
                    retries = 0 
                
                logging.warning(f"Retrying SBOM for {full_repo_name} in {sleep_duration:.2f}s.")
                time.sleep(sleep_duration)
                continue

            if response.status_code in [404, 403]:
                logging.warning(f"Could not retrieve SBOM for {full_repo_name}. Dependency graph might be disabled. Status: {response.status_code}")
                return None

            response.raise_for_status() 
            return response.json()

        except requests.exceptions.RequestException as e:
            logging.error(f"Network error fetching SBOM for {full_repo_name}: {e}. Giving up on this repo.")
            return None


def get_graphql_license_cache(session: requests.Session, owner: str, repo: str) -> Dict[str, Dict[str, str]]:
    """
    Fetches all dependencies via GraphQL and builds a cache of license info.
    Handles pagination for manifests, but fetches only the first page of dependencies per manifest.
    """
    logging.info(f"-> Building GraphQL license cache for {owner}/{repo}...")
    license_cache = {}
    base_delay = 5
    max_delay = 3600
    retries = 0
    
    manifests_after = None
    has_next_manifest = True

    while has_next_manifest:
        variables = {
            "owner": owner,
            "name": repo,
            "pageSize": GQL_PAGE_SIZE,
            "manifestsAfter": manifests_after,
        }
        
        try:
            response = session.post(GITHUB_GQL_URL, json={"query": GQL_DEPENDENCY_QUERY, "variables": variables})
            
            should_retry, sleep_duration = handle_api_retry(response, f"GQL for {owner}/{repo}")
            if should_retry:
                if sleep_duration == -1: 
                    sleep_duration = (base_delay * (2 ** retries)) + random.uniform(0, 1)
                    sleep_duration = min(sleep_duration, max_delay)
                    retries += 1
                else:
                    retries = 0
                
                logging.warning(f"Retrying GQL for {owner}/{repo} in {sleep_duration:.2f}s.")
                time.sleep(sleep_duration)
                continue 
            
            response.raise_for_status()
            data = response.json()
            
            retries = 0 

            if "errors" in data and "data" not in data:
                logging.error(f"-> GQL query for {owner}/{repo} failed. Errors: {data['errors']}")
                return {} 

            if "data" not in data or not data["data"].get("repository"):
                logging.warning(f"-> GQL returned no repository data for {owner}/{repo}. Skipping cache.")
                return {}
            
            repo_data = data["data"]["repository"]
            manifests = repo_data.get("dependencyGraphManifests", {})
            
            for manifest in manifests.get("nodes", []):
                dependencies = manifest.get("dependencies", {})
                for dep in dependencies.get("nodes", []):
                    if not dep or not dep.get("packageName"):
                        continue
                        
                    pkg_name = dep["packageName"]
                    license_info = dep.get("repository", {}).get("licenseInfo") if dep.get("repository") else None
                    repo_name = dep.get("repository", {}).get("nameWithOwner") if dep.get("repository") else None

                    license_cache[pkg_name] = {
                        "spdxId": license_info.get("spdxId") if license_info else None,
                        "name": license_info.get("name") if license_info else None,
                        "url": license_info.get("url") if license_info else None,
                        "repoName": repo_name
                    }
            
            manifest_page_info = manifests.get("pageInfo", {})
            manifests_after = manifest_page_info.get("endCursor")
            has_next_manifest = manifest_page_info.get("hasNextPage", False)
        
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error fetching GQL for {owner}/{repo}: {e}. Skipping cache.")
            return {}
            
    logging.info(f"-> GQL cache built for {owner}/{repo}, found {len(license_cache)} packages.")
    return license_cache


def parse_sbom_data(
    sbom_data: Dict[str, Any],
    repo_url: str,
    graphql_cache: Dict[str, Dict[str, str]]
) -> List[Tuple[str, ...]]:
    """
    Parses the SBOM JSON, enriches with GraphQL data, and extracts dependencies.
    """
    dependencies = []
    if not sbom_data or "sbom" not in sbom_data:
        return dependencies

    sbom = sbom_data["sbom"]
    packages = sbom.get("packages", [])
    
    repo_spdx_id = ""
    for rel in sbom.get("relationships", []):
        if rel.get("relationshipType") == "DESCRIBES":
            repo_spdx_id = rel.get("relatedSpdxElement")
            break

    for package in packages:
        if package.get("SPDXID") == repo_spdx_id:
            continue
            
        name = package.get("name", "N/A")
        version = package.get("versionInfo", "N/A")
        license_info = package.get("licenseConcluded", "N/A")

        purl = "N/A"
        pkg_manager = "N/A"
        if "externalRefs" in package:
            for ref in package["externalRefs"]:
                if ref.get("referenceType") == "purl":
                    purl = ref.get("referenceLocator", "N/A")
                    if purl.startswith("pkg:"):
                        manager_match = re.search(r"pkg:([^/]+)/", purl)
                        if manager_match:
                            pkg_manager = manager_match.group(1)
                    break
        
        dep_repo_name = "N/A"
        dep_license_url = "N/A"

        if license_info in ["N/A", "NOASSERTION"]:
            gql_data = graphql_cache.get(name)
            if gql_data:
                gql_spdx = gql_data.get("spdxId")
                gql_name = gql_data.get("name")
                gql_url = gql_data.get("url")
                
                dep_repo_name = gql_data.get("repoName") or "N/A"
                dep_license_url = gql_url or "N/A"
                
                if gql_spdx and gql_spdx not in ["N/A", "NOASSERTION", None]:
                    license_info = gql_spdx 
                elif gql_spdx == "NOASSERTION" and gql_name == "Other":
                    license_info = "NOASSERTION (Other)" 
        
        dependencies.append((
            name,
            version,
            repo_url,
            license_info,
            purl,
            pkg_manager,
            dep_repo_name,
            dep_license_url
        ))
        
    return dependencies


def main():
    """Main function to orchestrate the dependency analysis process."""
    parser = argparse.ArgumentParser(description="Analyze GitHub repository dependencies and export to CSV.")
    parser.add_argument(
        "--start-index", 
        type=int, 
        default=0, 
        help="The 0-based index to start processing from."
    )
    parser.add_argument(
        "--output", "-o", 
        type=str, 
        default=None, 
        help="Optional: Specify a custom output CSV filename. Default is dependencies_[ORG].csv"
    )
    # Arguments for Org and Token (CLI precedence over Env)
    parser.add_argument(
        "--org", 
        type=str, 
        default=None, 
        help="The GitHub organization name. Overrides GITHUB_ORG environment variable."
    )
    parser.add_argument(
        "--token", "-t", 
        type=str, 
        default=None, 
        help="The GitHub Personal Access Token (PAT). Overrides GITHUB_TOKEN environment variable."
    )
    args = parser.parse_args()
    
    start_index = args.start_index

    # --- Configuration Loading ---
    # 1. Load GITHUB_ORG (CLI or Env)
    github_org = args.org or os.getenv("GITHUB_ORG")
    if not github_org:
        logging.critical("Error: GitHub organization not provided. Use --org or set GITHUB_ORG environment variable.")
        sys.exit(1)

    # 2. Load GITHUB_TOKEN (CLI or Env)
    github_token = args.token or os.getenv("GITHUB_TOKEN")
    if not github_token:
        logging.critical("Error: GitHub token not provided. Use --token or set GITHUB_TOKEN environment variable.")
        sys.exit(1)
    # -----------------------------

    # Initialize Session
    try:
        session = get_github_session(github_token)
    except Exception as e:
        logging.critical(f"Error initializing GitHub session: {e}")
        sys.exit(1)

    if args.output:
        output_filename = args.output
    else:
        # *** FIXED: Use new default name format ***
        output_filename = f"dependencies_{github_org}.csv"
    
    logging.info(f"Starting dependency analysis for organization: {github_org}")
    logging.info(f"Output will be saved to: {output_filename}")

    all_repos = get_all_organization_repos(session, github_org)
    all_repos.sort(key=lambda r: r.get("full_name", "").lower())
    
    total_repos = len(all_repos)
    logging.info(f"Found a total of {total_repos} repositories.")
    
    if start_index >= total_repos:
        logging.info("Start index is beyond the total number of repositories. Nothing to do.")
        return

    file_mode = 'a' if start_index > 0 else 'w'
    write_header = start_index == 0
    
    try:
        with open(output_filename, mode=file_mode, newline="", encoding="utf-8") as csvfile:
            csv_writer = csv.writer(csvfile)
            
            if write_header:
                csv_writer.writerow([
                    "dependency_name",
                    "dependency_version",
                    "repository_url",
                    "license",
                    "package_url",
                    "package_manager",
                    "dependency_repo_name",
                    "dependency_license_url"
                ])

            repos_to_process = all_repos[start_index:]
            
            for i, repo in enumerate(repos_to_process, start=start_index):
                full_name = repo.get("full_name")
                repo_url = repo.get("html_url")
                
                if not full_name or not repo_url:
                    logging.warning(f"Skipping repo with missing data at index {i}.")
                    continue
                
                if repo.get('archived'):
                    logging.info(f"({i + 1}/{total_repos}) Skipping archived repo: {full_name}")
                    continue

                logging.info(f"({i + 1}/{total_repos}) Processing repo: {full_name}")
                
                try:
                    owner, repo_name = full_name.split('/')
                except ValueError:
                    logging.warning(f"-> Could not parse owner/repo from {full_name}. Skipping GQL.")
                    continue

                graphql_cache = get_graphql_license_cache(session, owner, repo_name)
                
                sbom_data = get_repo_sbom(session, full_name)
                
                if sbom_data:
                    dependencies = parse_sbom_data(sbom_data, repo_url, graphql_cache)
                    if dependencies:
                        csv_writer.writerows(dependencies)
                        logging.info(f"-> Found and wrote {len(dependencies)} dependencies for {full_name}.")
                    else:
                        logging.info(f"-> No external dependencies found for {full_name}.")
                else:
                    logging.info(f"-> Skipping {full_name} due to SBOM retrieval failure.")
                
                if (i + 1) < total_repos:
                    time.sleep(PROACTIVE_DELAY_SECONDS)
            
    except IOError as e:
        logging.critical(f"Could not write to file {output_filename}: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logging.warning(f"\nProcess interrupted by user. To resume, run with --start-index {i} and the appropriate --org/--token flags.")
        sys.exit(1)
        
    logging.info(f"✅ Script finished. All data has been saved to {output_filename}.")


if __name__ == "__main__":
    main()
