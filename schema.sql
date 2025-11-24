CREATE TABLE repos (
   organization TEXT,
   name TEXT,
   html_url TEXT,
   is_archived TEXT,
   visibility TEXT,
   last_push TEXT,
   description TEXT,
   topics TEXT,
   languages_bytes TEXT,
   custom_properties TEXT,
   contributors TEXT,
   Cpp_Conan_Lock TEXT,
   Cpp_Conan_Manifest TEXT,
   Cpp_vcpkg_Lock TEXT,
   Cpp_vcpkg_Manifest TEXT,
   Go_Gomodules_Lock TEXT,
   Go_Gomodules_Manifest TEXT,
   JavaScript_Bun_Lock TEXT,
   JavaScript_Bun_npm_pnpm_Yarn_Manifest TEXT,
   JavaScript_npm_Lock TEXT,
   JavaScript_pnpm_Lock TEXT,
   JavaScript_Yarn_Lock TEXT,
   PHP_Composer_Lock TEXT,
   PHP_Composer_Manifest TEXT,
   Python_PDM_Lock TEXT,
   Python_PDM_Poetry_Manifest TEXT,
   Python_Pipenv_Lock TEXT,
   Python_Pipenv_Manifest TEXT,
   Python_Poetry_Lock TEXT,
   Python_pip_Lock TEXT,
   Python_pip_Manifest TEXT,
   Ruby_Bundler_Lock TEXT,
   Ruby_Bundler_Manifest TEXT,
   Swift_SwiftPackageManager_Lock TEXT,
   Swift_SwiftPackageManager_Manifest TEXT
);

CREATE TABLE packages (
   name TEXT,
   version TEXT,
   importing_repo TEXT,
   licenses TEXT
);

CREATE TABLE licenses (
   name TEXT,
   allowed_status TEXT
);

.mode csv

.import licenses.csv licenses
.import all_deps.csv packages
.import all_repos.csv repos

DELETE FROM licenses WHERE rowid = 1;
DELETE FROM packages WHERE rowid = 1;
DELETE FROM repos WHERE rowid = 1;

-- Index to speed up finding problematic licenses
CREATE INDEX idx_licenses_status ON licenses(allowed_status);

-- Index to speed up filtering out archived repos
CREATE INDEX idx_repos_archived ON repos(is_archived);

-- This can *partially* help the repo join, but only if r.html_url is indexed
CREATE INDEX idx_repos_html_url ON repos(html_url);

