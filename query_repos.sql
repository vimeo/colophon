-- This query finds all non-archived repositories that contain one or more packages
-- with a 'STOP' or 'CAUTION' license. It returns all columns for each matching
-- repository, along with a count of its distinct affected packages.

-- A Common Table Expression (CTE) to first find and aggregate the affected repos
WITH AffectedRepoCounts AS (
    SELECT
        r.html_url,
        -- Count the number of *distinct* packages that are affected
        COUNT(DISTINCT p.name) AS affected_package_count
    FROM
        repos r
    JOIN
        -- Link repos to packages: based on your info, these are an exact match.
        -- This is more accurate and much faster than the old 'LIKE' join.
        packages p ON p.importing_repo = r.html_url
    WHERE
        -- Condition 1: Exclude archived repositories
        r.is_archived != 'TRUE'

        -- Condition 2: Filter for packages that have at least one 'STOP' or 'CAUTION' license
        AND (EXISTS (
            SELECT 1
            FROM licenses l
            WHERE
                (l.allowed_status = 'STOP' OR l.allowed_status = 'CAUTION' OR l.allowed_status = 'UNCLASSIFIED' OR l.allowed_status = 'UNDETECTED')
                -- This checks if the package's license string "mentions" the name of a bad license
                AND p.licenses LIKE '%' || l.name || '%'
        )
        --)

    GROUP BY
        -- Group the results by repo to get the count per repo
        r.html_url
)
-- Final SELECT statement
-- Get all details for the repos identified in the CTE
SELECT
    r.*,  -- Selects all columns from the 'repos' table
    arc.affected_package_count
FROM
    repos r
JOIN
    -- Join the full repos table with our list of affected repos
    AffectedRepoCounts arc ON r.html_url = arc.html_url
ORDER BY
    -- Sort the results to see the most affected repos first
    arc.affected_package_count DESC;
