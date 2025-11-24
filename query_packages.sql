-- This query lists every package instance that has at least one 'STOP' or 'CAUTION' license.
-- It is optimized to find the set of problematic packages FIRST, and then join that
-- smaller set against the repos table to reduce the number of expensive LIKE joins.

-- A Common Table Expression (CTE) to find all problematic packages first
WITH ProblematicPackages AS (
    SELECT
        p.name,
        p.version,
        p.importing_repo,
        p.licenses AS all_package_licenses,
        
        -- The specific problematic license and its status
        l.name AS problematic_license_name,
        l.allowed_status AS problematic_license_status
    FROM
        packages p
    JOIN
        -- This join finds all problematic licenses mentioned in the package's license string.
        -- This is an expensive (slow) join, so we do it first on the raw tables.
        licenses l ON p.licenses LIKE '%' || l.name || '%'
                  AND (l.allowed_status = 'STOP' OR l.allowed_status = 'CAUTION' OR l.allowed_status = 'UNCLASSIFIED' OR l.allowed_status = 'UNDETECTED')
)
-- Now, join the (much smaller) set of problematic packages against the repos
SELECT
    pp.name AS package_name,
    pp.version,
    pp.importing_repo,
    pp.all_package_licenses,
    pp.problematic_license_name,
    pp.problematic_license_status,
    
    -- All columns from the associated repo
    r.*
FROM
    ProblematicPackages pp
JOIN
    -- This is the second expensive join, but now we're only doing it
    -- for packages we *know* are problematic, not all packages.
    -- UPDATED: Changed from LIKE to = for a massive performance gain.
    repos r ON pp.importing_repo = r.html_url
           AND r.is_archived != 'TRUE'
ORDER BY
    -- Sort by repo, then package, then the problematic license
    r.html_url,
    pp.name,
    pp.problematic_license_name;


