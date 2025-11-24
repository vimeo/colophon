import os
import csv
import time
import requests
import argparse
import sys

# The base URL for the GitHub Enterprise Cloud API
API_BASE_URL = "https://api.github.com"

# Defines the manifest and lock files to check for.
# This list is sorted by Language, then Package Manager, then Type (Manifest/Lock)
# 'language': The language to check for in the repo's language list.
# 'column_name': The exact name for the CSV column.
# 'files_to_check': A list of file names to look for in the repo root.
MANIFEST_LOCK_FILES = [
    {'language': 'C++', 'column_name': 'C++ - Conan - Lock', 'files_to_check': ['conan.lock']},
    {'language': 'C++', 'column_name': 'C++ - Conan - Manifest', 'files_to_check': ['conanfile.py', 'conanfile.txt']},
    {'language': 'C++', 'column_name': 'C++ - vcpkg - Lock', 'files_to_check': ['vcpkg-lock.json']},
    {'language': 'C++', 'column_name': 'C++ - vcpkg - Manifest', 'files_to_check': ['vcpkg.json']},
    {'language': 'Go', 'column_name': 'Go - Go modules - Lock', 'files_to_check': ['go.sum']},
    {'language': 'Go', 'column_name': 'Go - Go modules - Manifest', 'files_to_check': ['go.mod']},
    {'language': 'JavaScript', 'column_name': 'JavaScript - Bun - Lock', 'files_to_check': ['bun.lock']},
    {'language': 'JavaScript', 'column_name': 'JavaScript - Bun, npm, pnpm, Yarn - Manifest', 'files_to_check': ['package.json']},
    {'language': 'JavaScript', 'column_name': 'JavaScript - npm - Lock', 'files_to_check': ['package-lock.json']},
    {'language': 'JavaScript', 'column_name': 'JavaScript - pnpm - Lock', 'files_to_check': ['pnpm-lock.yaml']},
    {'language': 'JavaScript', 'column_name': 'JavaScript - Yarn - Lock', 'files_to_check': ['yarn.lock']},
    {'language': 'PHP', 'column_name': 'PHP - Composer - Lock', 'files_to_check': ['composer.lock']},
    {'language': 'PHP', 'column_name': 'PHP - Composer - Manifest', 'files_to_check': ['composer.json']}, # FIXED: 'language' value corrected and 'column_name' added
    {'language': 'Python', 'column_name': 'Python - PDM - Lock', 'files_to_check': ['pdm.lock']},
    {'language': 'Python', 'column_name': 'Python - PDM, Poetry - Manifest', 'files_to_check': ['pyproject.toml']}, # FIXED: 'language' value corrected and 'column_name' added
    {'language': 'Python', 'column_name': 'Python - Pipenv - Lock', 'files_to_check': ['Pipfile.lock']},
    {'language': 'Python', 'column_name': 'Python - Pipenv - Manifest', 'files_to_check': ['Pipfile']},
    {'language': 'Python', 'column_name': 'Python - Poetry - Lock', 'files_to_check': ['poetry.lock']},
    {'language': 'Python', 'column_name': 'Python - pip - Lock', 'files_to_check': ['pylock.toml']},
    {'language': 'Python', 'column_name': 'Python - pip - Manifest', 'files_to_check': ['requirements.txt']},
    {'language': 'Ruby', 'column_name': 'Ruby - Bundler - Lock', 'files_to_check': ['Gemfile.lock']},
    {'language': 'Ruby', 'column_name': 'Ruby - Bundler - Manifest', 'files_to_check': ['Gemfile']},
    {'language': 'Swift', 'column_name': 'Swift - Swift Package Manager - Lock', 'files_to_check': ['Package.resolved']},
    {'language': 'Swift', 'column_name': 'Swift - Swift Package Manager - Manifest', 'files_to_check': ['Package.swift']},
]


def get_github_api_headers(token):
    """
    Constructs the headers required for GitHub API requests.

    Args:
        token (str): The GitHub personal access token.

    Returns:
        dict: A dictionary of headers for the API request.
    """
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

def make_api_request(url, headers, params=None, max_retries=5):
    """
    Makes a request to the GitHub API with error handling and rate limit management.

    Args:
        url (str): The full URL for the API endpoint.
        headers (dict): The request headers.
        params (dict, optional): The query parameters for the request. Defaults to None.
        max_retries (int, optional): The maximum number of times to retry the request. Defaults to 5.

    Returns:
        requests.Response: The response object from the API call.
    """
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            # Check for rate limit abuse
            if 'Retry-After' in response.headers:
                wait_time = int(response.headers['Retry-After'])
                print(f"Rate limit secondary abuse detected. Waiting for {wait_time} seconds.", file=sys.stderr)
                time.sleep(wait_time)
                continue # Retry the request

            # Check for primary rate limit exceeded
            if response.status_code == 403 and 'X-RateLimit-Remaining' in response.headers and int(response.headers['X-RateLimit-Remaining']) == 0:
                reset_time = int(response.headers.get('X-RateLimit-Reset', time.time() + 60))
                wait_time = max(reset_time - time.time(), 0) + 5 # Add a small buffer
                print(f"Primary rate limit exceeded. Waiting for {wait_time:.2f} seconds.", file=sys.stderr)
                time.sleep(wait_time)
                continue # Retry the request

            response.raise_for_status()  # Raise an exception for other bad status codes (4xx or 5xx)
            return response

        except requests.exceptions.RequestException as e:
            print(f"Request failed on attempt {attempt + 1}/{max_retries}: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                # Exponential backoff
                wait_time = 2 ** attempt
                print(f"Retrying in {wait_time} seconds...", file=sys.stderr)
                time.sleep(wait_time)
            else:
                print("Max retries reached. Aborting.", file=sys.stderr)
                raise
    return None


def get_paginated_data(url, headers):
    """
    Retrieves all items from a paginated GitHub API endpoint.

    Args:
        url (str): The initial URL for the paginated resource.
        headers (dict): The request headers.

    Returns:
        list: A list of all items retrieved from all pages.
    """
    all_items = []
    params = {'per_page': 100} # Request the maximum number of items per page
    
    while url:
        response = make_api_request(url, headers, params=params)
        if not response:
            break
            
        all_items.extend(response.json())
        
        # GitHub uses the 'Link' header for pagination
        if 'next' in response.links:
            url = response.links['next']['url']
            # Subsequent requests should not include params as they are in the URL
            params = None
        else:
            url = None
            
    return all_items


def get_org_repos(org_name, headers):
    """
    Fetches all repositories for a given organization.

    Args:
        org_name (str): The name of the GitHub organization.
        headers (dict): The request headers.

    Returns:
        list: A list of repository data dictionaries.
    """
    print(f"Fetching repositories for organization: {org_name}...")
    url = f"{API_BASE_URL}/orgs/{org_name}/repos"
    repos = get_paginated_data(url, headers)
    print(f"Found {len(repos)} repositories for {org_name}.")
    return repos

def get_repo_languages(repo_full_name, headers):
    """
    Fetches the language breakdown for a specific repository.

    Args:
        repo_full_name (str): The full name of the repository (e.g., 'org/repo').
        headers (dict): The request headers.

    Returns:
        tuple[dict, str]: A tuple containing the raw language dictionary and a formatted string.
    """
    url = f"{API_BASE_URL}/repos/{repo_full_name}/languages"
    try:
        response = make_api_request(url, headers)
        if response:
            languages_dict = response.json()
            if not languages_dict:
                return {}, "N/A"
            
            languages_str = ", ".join([f"{lang}: {bytes}" for lang, bytes in languages_dict.items()])
            return languages_dict, languages_str
    except requests.exceptions.RequestException:
        # If fetching languages fails, return empty data
        pass
    
    return {}, "Error fetching languages"

def get_root_files(repo_full_name, headers):
    """
    Retrieves the list of files and directories in the root of a repository.

    Args:
        repo_full_name (str): The full name of the repository (e.g., 'org/repo').
        headers (dict): The request headers.

    Returns:
        list: A list of item names (files/dirs) in the root, or an empty list on error.
    """
    url = f"{API_BASE_URL}/repos/{repo_full_name}/contents/"
    try:
        response = make_api_request(url, headers)
        if response:
            # We only need the names of the files/directories
            return [item['name'] for item in response.json()]
    except requests.exceptions.HTTPError as e:
        # An empty repo can return a 404, which is not an error in our logic flow.
        if e.response.status_code == 404:
            print(f"  - Note: Repo '{repo_full_name}' root appears to be empty or inaccessible.", file=sys.stderr)
            return []
        # Let make_api_request handle retries for other HTTP errors
        raise
    except requests.exceptions.RequestException as e:
        print(f"  - Request failed when getting root contents for {repo_full_name}: {e}", file=sys.stderr)
        return []
    return []

def get_repo_contributors(repo_full_name, headers):
    """
    Fetches a sorted list of contributor logins for a repository.

    Args:
        repo_full_name (str): The full name of the repository.
        headers (dict): The request headers.

    Returns:
        str: A comma-separated string of contributor logins, sorted by contributions descending.
    """
    print(f"  - Fetching contributors for {repo_full_name}...")
    url = f"{API_BASE_URL}/repos/{repo_full_name}/contributors"
    try:
        contributors_data = get_paginated_data(url, headers)
        if not contributors_data:
            return "N/A"
        
        # Sort contributors by the 'contributions' key in descending order
        contributors_data.sort(key=lambda x: x.get('contributions', 0), reverse=True)
        
        # Extract the login (username) for each contributor
        logins = [contributor['login'] for contributor in contributors_data]
        
        return ", ".join(logins)
    except Exception as e:
        # Handle cases where contributors list is empty or inaccessible (e.g., 204 No Content)
        if 'response' in locals() and contributors_data == []:
            return "N/A"
        print(f"  - Could not fetch contributors for {repo_full_name}: {e}", file=sys.stderr)
        return "Error fetching contributors"

def get_repo_custom_properties(repo_full_name, headers):
    """
    Fetches the custom properties for a specific repository.

    Args:
        repo_full_name (str): The full name of the repository (e.g., 'org/repo').
        headers (dict): The request headers.

    Returns:
        str: A formatted string of custom properties (e.g., "prop1: val1, prop2: val2a;val2b").
    """
    print(f"  - Fetching custom properties for {repo_full_name}...")
    url = f"{API_BASE_URL}/repos/{repo_full_name}/properties/values"
    try:
        response = make_api_request(url, headers)
        if response:
            properties = response.json()
            if not properties:
                return "N/A"
            
            formatted_props = []
            for prop in properties:
                prop_name = prop['property_name']
                prop_value = prop['value']
                
                if isinstance(prop_value, list):
                    # Join list values with a semi-colon
                    value_str = ";".join(prop_value)
                elif prop_value is None:
                    value_str = "None"
                else:
                    value_str = str(prop_value)
                
                formatted_props.append(f"{prop_name}: {value_str}")
            
            return ", ".join(formatted_props)
    except requests.exceptions.HTTPError as e:
        # 404 is expected if the org doesn't have custom properties enabled/set
        if e.response.status_code == 404:
            print(f"  - Note: No custom properties found for {repo_full_name} (404).", file=sys.stderr)
            return "N/A"
        # 403 can happen if the token doesn't have permissions
        if e.response.status_code == 403:
            print(f"  - Warning: Insufficient permissions for custom properties on {repo_full_name} (403).", file=sys.stderr)
            return "Permission Denied"
        # Re-raise other HTTP errors
        print(f"  - HTTP Error fetching custom properties for {repo_full_name}: {e}", file=sys.stderr)
        return "Error fetching properties"
    except requests.exceptions.RequestException as e:
        print(f"  - Error fetching custom properties for {repo_full_name}: {e}", file=sys.stderr)
        return "Error fetching properties"
    
    return "N/A" # Default case

def main():
    """
    Main function to orchestrate fetching repository data and writing it to a CSV.
    """
    parser = argparse.ArgumentParser(
        description="Fetch repository data from a single GitHub organization and export to a CSV file."
    )
    # Organization is now a named argument
    parser.add_argument(
        "--org",
        type=str,
        default=None,
        help="The GitHub organization name to fetch repositories from (single organization). Falls back to GITHUB_ORG environment variable."
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="The name of the output CSV file. Defaults to 'repos_{org_name}.csv'."
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Global repository index to start processing from (for resuming). Defaults to 0."
    )
    parser.add_argument(
        "-t", "--token",
        type=str,
        default=None,
        help="Optional: GitHub Personal Access Token (PAT). Overrides GITHUB_TOKEN environment variable."
    )
    args = parser.parse_args()

    # --- Credential and Organization Retrieval ---

    # 1. Get GitHub token: Check CLI argument first, then environment variable
    github_token = args.token or os.getenv("GITHUB_TOKEN")
    
    if not github_token:
        print("Error: GitHub Personal Access Token not found.", file=sys.stderr)
        print("Please provide it using the --token argument or the GITHUB_TOKEN environment variable.", file=sys.stderr)
        sys.exit(1)
        
    # 2. Get Organization: Check --org argument first, then GITHUB_ORG environment variable
    org_name = args.org or os.getenv("GITHUB_ORG")
    
    if not org_name:
        print("Error: Organization name must be provided via the --org argument or the GITHUB_ORG environment variable.", file=sys.stderr)
        sys.exit(1)
            
    # Determine the output filename (now single organization only)
    output_filename = args.output
    if output_filename is None:
        # Default filename format updated to repos_{org_name}.csv
        output_filename = f"repos_{org_name}.csv" 
        print(f"No output file specified. Defaulting to '{output_filename}'")
    
    headers = get_github_api_headers(github_token)

    # Define the final CSV fieldnames
    base_fieldnames = [
        "organization", "repository_name", "html_url", "is_archived", 
        "visibility", "last_push", "description", "topics", "languages_bytes", 
        "custom_properties", "contributors"
    ]
    # This line now executes successfully because all entries in MANIFEST_LOCK_FILES 
    # are guaranteed to have the 'column_name' key.
    manifest_lock_fieldnames = [item['column_name'] for item in MANIFEST_LOCK_FILES] 
    fieldnames = base_fieldnames + manifest_lock_fieldnames

    # Fetch repositories for the single organization
    print(f"Fetching all repository lists for {org_name}...")
    all_repos_with_org = []
    repos_list = get_org_repos(org_name, headers)
    for repo in repos_list:
        # Store the organization name explicitly for the CSV column
        repo['organization_name_for_csv'] = org_name  
    all_repos_with_org.extend(repos_list)
    
    # Sort all repos globally by full_name for a consistent processing order
    all_repos_with_org.sort(key=lambda r: r['full_name'])
    
    total_repos = len(all_repos_with_org)
    print(f"Found a total of {total_repos} repositories in {org_name}.")
    
    if args.start_index > 0:
        print(f"Resuming from global index {args.start_index}...")

    # Determine file mode and write header if needed
    file_mode = 'a' if args.start_index > 0 else 'w'
    try:
        with open(output_filename, file_mode, newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            if args.start_index == 0:
                writer.writeheader()
            
            # Main processing loop
            for i, repo in enumerate(all_repos_with_org):
                # Check if we should skip this repo
                if i < args.start_index:
                    continue

                # Extract data
                repo_name = repo['name']
                repo_full_name = repo['full_name']
                org = repo['organization_name_for_csv'] # Get the org name we stored
                last_push = repo['pushed_at']
                description = repo.get('description') or "" # Handle null
                html_url = repo['html_url']
                default_branch = repo['default_branch']
                visibility = repo['visibility']
                topics_list = repo.get('topics', [])
                topics_str = ", ".join(topics_list)
                
                # Update progress message to use global index
                print(f"Processing repo {i+1}/{total_repos} (Global Index {i}): {repo_full_name}")
                
                languages_dict, languages_str = get_repo_languages(repo_full_name, headers)
                custom_properties = get_repo_custom_properties(repo_full_name, headers)
                
                # Initialize all manifest/lock file data as blank
                manifest_lock_data = {col: "" for col in manifest_lock_fieldnames}

                # Only check for files if the repo has languages defined
                if languages_dict:
                    print(f"  - Checking for manifest/lock files...")
                    root_files_set = set(get_root_files(repo_full_name, headers))
                    base_file_url = f"{html_url}/blob/{default_branch}/"
                    repo_languages_set = set(languages_dict.keys())

                    for file_spec in MANIFEST_LOCK_FILES:
                        # Check if the repo's languages match the file's language
                        if file_spec['language'] in repo_languages_set:
                            found_file = None
                            # Check for any of the possible file names
                            for file_name in file_spec['files_to_check']:
                                if file_name in root_files_set:
                                    found_file = file_name
                                    break
                            
                            # If we found the file, create the full URL
                            if found_file:
                                manifest_lock_data[file_spec['column_name']] = f"{base_file_url}{found_file}"

                # Fetch contributors for ALL repos
                contributors = get_repo_contributors(repo_full_name, headers)

                # Create the base data row
                repo_data_row = {
                    "organization": org,
                    "repository_name": repo_name,
                    "html_url": html_url,
                    "is_archived": repo['archived'],
                    "visibility": visibility,
                    "last_push": last_push,
                    "description": description,
                    "topics": topics_str,
                    "languages_bytes": languages_str,
                    "custom_properties": custom_properties,
                    "contributors": contributors,
                }
                
                # Add all the dynamic manifest/lock data
                repo_data_row.update(manifest_lock_data)
                
                # Incremental write
                writer.writerow(repo_data_row)
                csvfile.flush() # Ensure it's written to disk immediately

    except IOError as e:
        print(f"Error writing to file '{output_filename}': {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nSuccessfully processed repositories. Data saved to '{output_filename}'.")

if __name__ == "__main__":
    main()
